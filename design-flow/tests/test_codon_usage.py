from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from design_flow.codon_usage import (
    SENSE_CODONS,
    build_codon_usage,
    configure_mrna_codon_generation,
    write_codon_usage,
)
from design_flow.product_design import _load_codon_usage
from design_flow.product_specs import MRNA_SPEC_RELATIVE


class CodonUsageTests(unittest.TestCase):
    @staticmethod
    def _write_fixture(path: Path) -> str:
        complete_codon_coverage = "".join(SENSE_CODONS) + "TAA"
        records = [
            (
                ">gene1-long [gene=ONE] [db_xref=GeneID:1] [gbkey=CDS]",
                complete_codon_coverage,
            ),
            (">gene1-short [gene=ONE] [db_xref=GeneID:1] [gbkey=CDS]", "ATGTAA"),
            (">gene2 [gene=TWO] [db_xref=GeneID:2] [gbkey=CDS]", "ATGGCTTAA"),
            (
                ">partial [db_xref=GeneID:3] [gbkey=CDS] [partial=3']",
                "ATGGCTTAA",
            ),
            (
                ">exception [db_xref=GeneID:4] [gbkey=CDS] [exception=selenocysteine]",
                "ATGTGATGG",
            ),
            (
                ">mitochondrial [db_xref=GeneID:5] [gbkey=CDS] [transl_table=2]",
                "ATGGCTTAA",
            ),
            (">ambiguous [db_xref=GeneID:6] [gbkey=CDS]", "ATGNNNTAA"),
            (">internal-stop [db_xref=GeneID:7] [gbkey=CDS]", "ATGTAAGCTTAA"),
            (">no-gene-id [gbkey=CDS]", "ATGGCTTAA"),
            (">pseudo [db_xref=GeneID:8] [gbkey=CDS] [pseudo=true]", "ATGGCTTAA"),
            (
                ">benign-evidence [db_xref=GeneID:9] [gbkey=CDS] "
                "[exception=annotated by transcript or proteomic data]",
                "ATGGCCTAA",
            ),
        ]
        with gzip.open(path, "wt", encoding="ascii", newline="\n") as handle:
            for header, sequence in records:
                handle.write(f"{header}\n{sequence}\n")
        return hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()

    def test_builds_gene_balanced_table_with_filter_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            source = Path(temporary_dir) / "fixture.fna.gz"
            expected_md5 = self._write_fixture(source)
            table, audit = build_codon_usage(
                source,
                species="fixture species",
                taxon_id=123,
                assembly="GCF_fixture.1",
                annotation_release="RS_fixture",
                source_url="https://example.test/fixture.fna.gz",
                expected_md5=expected_md5,
            )

            self.assertEqual(set(table["codon_frequencies"]), set(SENSE_CODONS))
            self.assertAlmostEqual(sum(table["codon_frequencies"].values()), 1.0)
            self.assertEqual(table["codon_counts"]["ATG"], 3)
            self.assertEqual(table["codon_counts"]["GCT"], 2)
            self.assertEqual(audit["selected_sense_codons"], 65)
            self.assertEqual(audit["records"]["total_fasta_records"], 11)
            self.assertEqual(audit["records"]["valid_standard_cds_records"], 4)
            self.assertEqual(audit["records"]["selected_cds_records"], 3)
            self.assertEqual(audit["records"]["selected_unique_gene_ids"], 3)
            self.assertEqual(
                audit["records"]["rejected_records_by_reason"],
                {
                    "ambiguous_or_invalid_base": 1,
                    "internal_stop": 1,
                    "missing_gene_id": 1,
                    "nonstandard_translation_table": 1,
                    "partial": 1,
                    "pseudo": 1,
                    "translation_exception": 1,
                },
            )

    def test_written_table_is_accepted_by_stage6_loader(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source = root / "fixture.fna.gz"
            expected_md5 = self._write_fixture(source)
            output = root / "codon-usage.json"
            audit = root / "codon-usage.audit.json"
            result = write_codon_usage(
                source,
                output,
                audit,
                species="fixture species",
                taxon_id=123,
                assembly="GCF_fixture.1",
                annotation_release="RS_fixture",
                source_url="https://example.test/fixture.fna.gz",
                expected_md5=expected_md5,
            )

            loaded = _load_codon_usage(output)
            self.assertEqual(loaded["species"], "fixture species")
            self.assertEqual(result["selected_cds_records"], 3)
            self.assertEqual(
                json.loads(audit.read_text(encoding="utf-8"))["codon_table_sha256"],
                result["codon_table_sha256"],
            )

    def test_rejects_source_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            source = Path(temporary_dir) / "fixture.fna.gz"
            self._write_fixture(source)
            with self.assertRaisesRegex(ValueError, "source MD5 mismatch"):
                build_codon_usage(
                    source,
                    species="fixture species",
                    taxon_id=123,
                    assembly="GCF_fixture.1",
                    annotation_release="RS_fixture",
                    source_url="https://example.test/fixture.fna.gz",
                    expected_md5="0" * 32,
                )

    def test_configures_exploratory_generation_and_archives_previous_spec(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source_project = root / "source"
            runtime = root / "runtime"
            source_project.mkdir()
            specification_path = runtime / MRNA_SPEC_RELATIVE
            specification_path.parent.mkdir(parents=True)
            configuration_path = source_project / "project.json"
            configuration_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "project_id": "fixture-project",
                        "expected_protein_count": 1,
                        "runtime_root": str(runtime),
                        "inputs": {
                            "amino_acid_fasta": "input/proteins.fasta",
                            "nucleotide_fasta": "input/cds.fasta",
                        },
                        "context": {
                            "product_modalities": ["mrna"],
                            "mrna_target_species": "cattle (Bos taurus)",
                        },
                    }
                ),
                encoding="utf-8",
            )
            specification_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "stage_id": "mrna_product_design",
                        "mode": "exploratory",
                        "target_context": {"species": "cattle (Bos taurus)"},
                        "generation": {
                            "status": "disabled",
                            "seed": 1,
                            "designs_per_candidate": 1,
                            "search_multiplier": 1,
                        },
                        "policy": {"allow_as_release_gate": False},
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            source = runtime / "source.fna.gz"
            expected_md5 = self._write_fixture(source)
            table_path = runtime / "input/stage6/codon-usage.json"
            audit_path = runtime / "input/stage6/codon-usage.audit.json"
            write_codon_usage(
                source,
                table_path,
                audit_path,
                species="Bos taurus",
                taxon_id=9913,
                assembly="GCF_fixture.1",
                annotation_release="RS_fixture",
                source_url="https://example.test/fixture.fna.gz",
                expected_md5=expected_md5,
            )

            configured = configure_mrna_codon_generation(
                configuration_path,
                table_path,
                designs_per_candidate=4,
                search_multiplier=32,
                seed=42,
            )

            updated = json.loads(specification_path.read_text(encoding="utf-8"))
            self.assertEqual(
                updated["codon_usage_table_path"],
                "input/stage6/codon-usage.json",
            )
            self.assertEqual(
                updated["generation"],
                {
                    "codon_usage_file_sha256": configured["codon_usage_file_sha256"],
                    "configuration_mode": "exploratory_mock",
                    "designs_per_candidate": 4,
                    "search_multiplier": 32,
                    "seed": 42,
                    "status": "enabled",
                },
            )
            self.assertTrue(Path(configured["history_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
