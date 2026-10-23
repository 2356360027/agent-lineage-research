"""Synthetic software witnesses, not model responses or research samples.

Only newly created :memory: databases are modified. Candidate SQL comes from the
fixed development config; these checks do not approve its natural-language
contracts. The four DDL statements were copied from the existing local schema
record runtime/runs/sqlglot-repro-audit-20261012-001/schemas.json (toxicology).
Target SQL was copied from the revised q200/q234 records of the existing Arcwise
compatibility audit. No private data rows, generated artifacts or network calls.

Schema-valid synthetic records need not be chemically realistic. Passing these
finite witnesses is not a general SQL equivalence proof or a new method result.
"""
from pathlib import Path
import sqlite3
import unittest

from runtime import bird_readonly_audit as audit
from runtime import cache_intent_dev_package as package


DDL = (
    """CREATE TABLE `atom` (
  `atom_id` TEXT NOT NULL,
  `molecule_id` TEXT DEFAULT NULL,
  `element` TEXT DEFAULT NULL,
  PRIMARY KEY (`atom_id`),
  FOREIGN KEY (`molecule_id`) REFERENCES `molecule` (`molecule_id`)
)""",
    """CREATE TABLE `bond` (
  `bond_id` TEXT NOT NULL,
  `molecule_id` TEXT DEFAULT NULL,
  `bond_type` TEXT DEFAULT NULL,
  PRIMARY KEY (`bond_id`),
  FOREIGN KEY (`molecule_id`) REFERENCES `molecule` (`molecule_id`)
)""",
    """CREATE TABLE `connected` (
  `atom_id` TEXT NOT NULL,
  `atom_id2` TEXT NOT NULL,
  `bond_id` TEXT DEFAULT NULL,
  PRIMARY KEY (`atom_id`,`atom_id2`),
  FOREIGN KEY (`atom_id`) REFERENCES `atom` (`atom_id`) ON DELETE CASCADE ON UPDATE CASCADE,
  FOREIGN KEY (`atom_id2`) REFERENCES `atom` (`atom_id`) ON DELETE CASCADE ON UPDATE CASCADE,
  FOREIGN KEY (`bond_id`) REFERENCES `bond` (`bond_id`) ON DELETE CASCADE ON UPDATE CASCADE
)""",
    """CREATE TABLE `molecule` (
  `molecule_id` TEXT NOT NULL,
  `label` TEXT DEFAULT NULL,
  PRIMARY KEY (`molecule_id`)
)""",
)

TARGET_SQL = {
    200: "SELECT DISTINCT T2.molecule_id FROM bond AS T1 INNER JOIN molecule AS T2 "
         "ON T1.molecule_id = T2.molecule_id WHERE T1.bond_type = '#' AND T2.label = '+'",
    234: "SELECT COUNT(DISTINCT bond_id) FROM connected WHERE atom_id = 'TR009_12' OR atom_id2 = 'TR009_12'",
}


def typed(values):
    return [[audit.typed(value) for value in row] for row in values]


def bag(rows):
    return audit.fingerprints(rows)["bag_sha256"]


class CacheWitnessTests(unittest.TestCase):
    def setUp(self):
        plan = package.read_json(Path(package.__file__).parents[1] / "configs/cache_intent_pairs_dev_v1.json")
        package.validate_plan(plan)
        self.histories = {target["question_id"]: target["histories"] for target in plan["targets"]}

    def empty_database(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA foreign_keys = ON")
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone(), (1,))
        for statement in DDL:
            conn.execute(statement)
        return conn

    def freeze_valid_fixture(self, conn):
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone(), (1,))
        attached = conn.execute("PRAGMA database_list").fetchall()
        self.assertIn((0, "main", ""), attached)
        # integrity_check may instantiate SQLite's own temporary schema.
        self.assertTrue(all(name in ("main", "temp") and filename == ""
                            for _, name, filename in attached))
        conn.commit()
        conn.execute("PRAGMA query_only = ON")
        self.assertEqual(conn.execute("PRAGMA query_only").fetchone(), (1,))
        return conn

    def molecule_world(self, second_label):
        conn = self.empty_database()
        conn.executemany("INSERT INTO molecule VALUES (?, ?)", [("M1", "+"), ("M2", second_label)])
        conn.executemany("INSERT INTO atom VALUES (?, ?, ?)",
                         [(f"M1_{i}", "M1", "C") for i in range(1, 5)] +
                         [(f"M2_{i}", "M2", "C") for i in range(1, 3)])
        # M1 has repeated qualifying bonds and a nonqualifying bond: EXISTS,
        # DISTINCT and 'all bonds triple' cannot be silently conflated.
        conn.executemany("INSERT INTO bond VALUES (?, ?, ?)",
                         [("M1b1", "M1", "#"), ("M1b2", "M1", "#"),
                          ("M1b3", "M1", "-"), ("M2b1", "M2", "#")])
        conn.executemany("INSERT INTO connected VALUES (?, ?, ?)",
                         [("M1_1", "M1_2", "M1b1"), ("M1_2", "M1_3", "M1b2"),
                          ("M1_3", "M1_4", "M1b3"), ("M2_1", "M2_2", "M2b1")])
        return self.freeze_valid_fixture(conn)

    def bond_world(self, third_incident, extra_mirror_and_null=False):
        conn = self.empty_database()
        conn.execute("INSERT INTO molecule VALUES ('TR009', '+')")
        conn.executemany("INSERT INTO atom VALUES (?, ?, ?)",
                         [(f"TR009_{i}", "TR009", "C") for i in (1, 2, 3, 4, 5, 12)])
        conn.executemany("INSERT INTO bond VALUES (?, ?, ?)",
                         [(f"b{i}", "TR009", "-") for i in (1, 2, 3)])
        edges = [("TR009_12", "TR009_1", "b1"), ("TR009_2", "TR009_12", "b2"),
                 ("TR009_3", "TR009_12" if third_incident else "TR009_4", "b3")]
        if extra_mirror_and_null:
            # Separate endpoint-pair keys: schema-valid duplicate bond identity
            # plus a nullable bond reference. This is not a new chemical sample.
            edges += [("TR009_1", "TR009_12", "b1"), ("TR009_5", "TR009_12", None)]
        conn.executemany("INSERT INTO connected VALUES (?, ?, ?)", edges)
        return self.freeze_valid_fixture(conn)

    def query(self, conn, sql):
        return typed(conn.execute(sql).fetchall())

    def scope_world(self, second_bond_molecule):
        """DDL-valid counterexample, not a chemically realistic observation."""
        conn = self.empty_database()
        conn.executemany("INSERT INTO molecule VALUES (?, '+')", [("TR009",), ("OTHER",)])
        conn.executemany("INSERT INTO atom VALUES (?, 'TR009', 'C')",
                         [("TR009_12",), ("TR009_1",), ("TR009_2",)])
        conn.executemany("INSERT INTO bond VALUES (?, ?, '-')",
                         [("b1", "TR009"), ("b2", second_bond_molecule)])
        conn.executemany("INSERT INTO connected VALUES (?, ?, ?)",
            [("TR009_12", "TR009_1", "b1"), ("TR009_1", "TR009_12", "b1"),
             ("TR009_2", "TR009_12", "b2")])
        return self.freeze_valid_fixture(conn)

    def scoped_count(self, conn):
        # Test-only post-hoc scope interpretation. Never overwrite old TARGET_SQL
        # or inject this evaluator query into agent prompts.
        return self.query(conn, "SELECT COUNT(DISTINCT c.bond_id) FROM connected c "
            "JOIN bond b ON b.bond_id=c.bond_id WHERE b.molecule_id='TR009' "
            "AND (c.atom_id='TR009_12' OR c.atom_id2='TR009_12')")

    def test_scope_scalar_cache_equal_but_scoped_target_differs_under_ddl(self):
        inside, outside = self.scope_world("TR009"), self.scope_world("OTHER")
        self.assertEqual(self.history(inside, 234, 0), typed([(2,)]))
        self.assertEqual(self.history(inside, 234, 0), self.history(outside, 234, 0))
        self.assertEqual(self.scoped_count(inside), typed([(2,)]))
        self.assertEqual(self.scoped_count(outside), typed([(1,)]))
        # Both worlds pass foreign_key_check; naming does not enforce this join.

    def test_scope_id_cache_needs_membership_information_but_supports_supplement(self):
        for owner in ("TR009", "OTHER", None):
            with self.subTest(owner=owner):
                conn = self.scope_world(owner)
                ids = [r[0][1] for r in self.history(conn, 234, 1)]
                self.assertEqual(set(ids), {"b1", "b2"})
                placeholders = ",".join("?" for _ in ids)
                supplemented = typed(conn.execute(
                    f"SELECT COUNT(DISTINCT bond_id) FROM bond WHERE molecule_id=? "
                    f"AND bond_id IN ({placeholders})", ["TR009", *ids]).fetchall())
                self.assertEqual(supplemented, self.scoped_count(conn))
                if owner != "TR009":
                    self.assertNotEqual(typed([(len(ids),)]), self.scoped_count(conn))

    def test_scope_zero_incidence_count_is_safe_even_without_global_invariant(self):
        conn = self.freeze_valid_fixture(self.empty_database())
        self.assertEqual(self.history(conn, 234, 0), typed([(0,)]))
        self.assertEqual(self.scoped_count(conn), typed([(0,)]))
        # Empty incidence is a boundary exception to "always needs a scope read".

    def target(self, conn, qid):
        return self.query(conn, TARGET_SQL[qid])

    def history(self, conn, qid, index):
        return self.query(conn, self.histories[qid][index]["sql"])

    def assert_valid_direct_and_repair(self, conn, qid):
        expected = self.target(conn, qid)
        self.assertEqual(bag(self.history(conn, qid, 0)), bag(expected))
        repair = self.histories[qid][1]
        transformed = package.transform_rows(self.history(conn, qid, 1), repair["transform"])
        self.assertEqual(bag(transformed), bag(expected))

    def test_q200_same_id_only_cache_different_targets(self):
        all_positive, mixed_labels = self.molecule_world("+"), self.molecule_world("-")
        left_cache, right_cache = self.history(all_positive, 200, 2), self.history(mixed_labels, 200, 2)
        left_target, right_target = self.target(all_positive, 200), self.target(mixed_labels, 200)
        self.assertEqual(bag(left_cache), bag(right_cache))
        self.assertEqual(bag(left_cache), bag(typed([("M1",), ("M2",)])))
        self.assertNotEqual(bag(left_target), bag(right_target))
        self.assertEqual(bag(left_cache), bag(left_target))  # Accidental safe-looking snapshot.
        self.assertNotEqual(bag(right_cache), bag(right_target))
        for conn in (all_positive, mixed_labels): self.assert_valid_direct_and_repair(conn, 200)

    def test_q234_same_total_cache_different_incidence_targets(self):
        two_incident, three_incident = self.bond_world(False), self.bond_world(True)
        left_cache, right_cache = self.history(two_incident, 234, 2), self.history(three_incident, 234, 2)
        left_target, right_target = self.target(two_incident, 234), self.target(three_incident, 234)
        self.assertEqual(left_cache, right_cache)
        self.assertEqual(left_cache, typed([(3,)]))
        self.assertEqual(left_target, typed([(2,)])); self.assertEqual(right_target, typed([(3,)]))
        self.assertNotEqual(left_cache, left_target)
        self.assertEqual(right_cache, right_target)  # Equality does not certify applicability.
        for conn in (two_incident, three_incident): self.assert_valid_direct_and_repair(conn, 234)

    def test_q200_insufficient_cache_can_support_augmented_reuse_with_label_read(self):
        for label in ("+", "-", None):
            conn = self.molecule_world(label)
            cached_ids = [row[0][1] for row in self.history(conn, 200, 2)]
            # Explicit new base-table access, not a cache-only transformation.
            placeholders = ",".join("?" for _ in cached_ids)
            labelled = typed(conn.execute(f"SELECT molecule_id, label FROM molecule WHERE molecule_id IN ({placeholders})",
                                          cached_ids).fetchall())
            repaired = package.transform_rows(labelled, "filter_positive_label_project_id")
            self.assertEqual(bag(repaired), bag(self.target(conn, 200)))

    def test_q200_exists_distinct_and_all_bonds_are_different_contracts(self):
        conn = self.molecule_world("-")
        expected = self.target(conn, 200)
        no_distinct = self.query(conn, TARGET_SQL[200].replace("SELECT DISTINCT", "SELECT"))
        all_bonds = self.query(conn, "SELECT m.molecule_id FROM molecule AS m WHERE m.label='+' "
                               "AND EXISTS (SELECT 1 FROM bond b WHERE b.molecule_id=m.molecule_id) "
                               "AND NOT EXISTS (SELECT 1 FROM bond b WHERE b.molecule_id=m.molecule_id AND b.bond_type<>'#')")
        equivalent_exists = self.query(conn, "SELECT m.molecule_id FROM molecule AS m WHERE m.label='+' "
                                       "AND EXISTS (SELECT 1 FROM bond b WHERE b.molecule_id=m.molecule_id AND b.bond_type='#')")
        self.assertEqual(expected, typed([("M1",)]))
        self.assertNotEqual(bag(no_distinct), bag(expected))
        self.assertEqual(audit.fingerprints(no_distinct)["duplicate_rows"], 1)
        self.assertEqual(all_bonds, [])
        self.assertEqual(bag(equivalent_exists), bag(expected))

    def test_q234_both_endpoints_dedup_and_null_handling(self):
        conn = self.bond_world(False, extra_mirror_and_null=True)
        expected = self.target(conn, 234)
        only_first = self.query(conn, "SELECT COUNT(DISTINCT bond_id) FROM connected WHERE atom_id='TR009_12'")
        count_rows = self.query(conn, "SELECT COUNT(*) FROM connected WHERE atom_id='TR009_12' OR atom_id2='TR009_12'")
        count_nonnull_rows = self.query(conn, "SELECT COUNT(bond_id) FROM connected WHERE atom_id='TR009_12' OR atom_id2='TR009_12'")
        self.assertEqual(expected, typed([(2,)]))
        self.assertEqual(only_first, typed([(1,)]))
        self.assertEqual(count_rows, typed([(4,)]))
        self.assertEqual(count_nonnull_rows, typed([(3,)]))
        self.assert_valid_direct_and_repair(conn, 234)

    def test_empty_success_is_empty_id_list_but_scalar_zero_count(self):
        conn = self.freeze_valid_fixture(self.empty_database())
        self.assertEqual(self.target(conn, 200), [])
        self.assertEqual(self.target(conn, 234), typed([(0,)]))
        self.assertNotEqual(bag([]), bag(typed([(0,)])))
        self.assert_valid_direct_and_repair(conn, 200)
        self.assert_valid_direct_and_repair(conn, 234)

    def test_in_memory_schema_enforces_foreign_keys_and_pair_primary_key(self):
        conn = self.empty_database()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO bond VALUES ('bad', 'missing_molecule', '#')")
        conn.execute("INSERT INTO molecule VALUES ('M', '+')")
        conn.executemany("INSERT INTO atom VALUES (?, 'M', 'C')", [("a1",), ("a2",)])
        conn.execute("INSERT INTO bond VALUES ('b', 'M', '#')")
        conn.execute("INSERT INTO connected VALUES ('a1', 'a2', 'b')")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO connected VALUES ('a1', 'a2', 'b')")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO connected VALUES ('missing_atom', 'a2', 'b')")
        self.freeze_valid_fixture(conn)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM connected")

    def test_bag_scoring_preserves_row_pairings_duplicates_types_but_not_order(self):
        original = typed([("A", 1), ("B", 2)])
        swapped_values = typed([("A", 2), ("B", 1)])
        reversed_rows = list(reversed(original))
        self.assertNotEqual(bag(original), bag(swapped_values))
        self.assertNotEqual(bag(original), bag(original + [original[0]]))
        self.assertEqual(bag(original), bag(reversed_rows))
        self.assertNotEqual(audit.fingerprints(original)["sequence_sha256"],
                            audit.fingerprints(reversed_rows)["sequence_sha256"])
        self.assertNotEqual(bag(typed([(1,)])), bag(typed([("1",)])))
        self.assertNotEqual(bag(typed([(None,)])), bag(typed([(0,)])))


if __name__ == "__main__":
    unittest.main()
