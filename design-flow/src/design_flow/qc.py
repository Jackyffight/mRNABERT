"""Translation validation and dependency-free baseline sequence metrics."""

from __future__ import annotations

from collections import Counter
import hashlib
import math

from .domain import FastaRecord, ProteinAnalysis, QCIssue


CANONICAL_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
VALID_NUCLEOTIDES = frozenset("ACGTN")
HYDROPHOBIC_AMINO_ACIDS = frozenset("AVILMFWY")
CHARGED_AMINO_ACIDS = frozenset("DEKRH")

# Average residue masses in a peptide chain. One water molecule is added for the termini.
RESIDUE_MASS = {
    "A": 71.0788,
    "C": 103.1388,
    "D": 115.0886,
    "E": 129.1155,
    "F": 147.1766,
    "G": 57.0519,
    "H": 137.1411,
    "I": 113.1594,
    "K": 128.1741,
    "L": 113.1594,
    "M": 131.1926,
    "N": 114.1038,
    "P": 97.1167,
    "Q": 128.1307,
    "R": 156.1875,
    "S": 87.0782,
    "T": 101.1051,
    "V": 99.1326,
    "W": 186.2132,
    "Y": 163.1760,
}

CODON_TABLE = {
    "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L",
    "TCT": "S", "TCC": "S", "TCA": "S", "TCG": "S",
    "TAT": "Y", "TAC": "Y", "TAA": "*", "TAG": "*",
    "TGT": "C", "TGC": "C", "TGA": "*", "TGG": "W",
    "CTT": "L", "CTC": "L", "CTA": "L", "CTG": "L",
    "CCT": "P", "CCC": "P", "CCA": "P", "CCG": "P",
    "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q",
    "CGT": "R", "CGC": "R", "CGA": "R", "CGG": "R",
    "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M",
    "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T",
    "AAT": "N", "AAC": "N", "AAA": "K", "AAG": "K",
    "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R",
    "GTT": "V", "GTC": "V", "GTA": "V", "GTG": "V",
    "GCT": "A", "GCC": "A", "GCA": "A", "GCG": "A",
    "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E",
    "GGT": "G", "GGC": "G", "GGA": "G", "GGG": "G",
}


def _issue(severity: str, code: str, message: str, protein_id: str) -> QCIssue:
    return QCIssue(severity=severity, code=code, message=message, protein_id=protein_id)


def normalize_amino_acid(sequence: str, protein_id: str) -> tuple[str, list[QCIssue]]:
    normalized = "".join(sequence.split()).upper()
    issues: list[QCIssue] = []
    if normalized.endswith("*"):
        normalized = normalized[:-1]
        issues.append(_issue("warning", "aa_terminal_stop_removed", "Removed terminal '*'", protein_id))
    if "*" in normalized:
        issues.append(_issue("error", "aa_internal_stop", "Amino-acid sequence contains an internal '*'", protein_id))
    invalid = sorted(set(normalized) - CANONICAL_AMINO_ACIDS - {"*"})
    if invalid:
        issues.append(
            _issue(
                "error",
                "aa_invalid_symbols",
                f"Unsupported amino-acid symbols: {''.join(invalid)}",
                protein_id,
            )
        )
    if not normalized:
        issues.append(_issue("error", "aa_empty", "Amino-acid sequence is empty", protein_id))
    return normalized, issues


def normalize_nucleotide(sequence: str, protein_id: str) -> tuple[str, list[QCIssue]]:
    compact = "".join(sequence.split()).upper()
    issues: list[QCIssue] = []
    if "U" in compact:
        compact = compact.replace("U", "T")
        issues.append(_issue("warning", "rna_normalized", "Converted U to T for CDS analysis", protein_id))
    invalid = sorted(set(compact) - VALID_NUCLEOTIDES)
    if invalid:
        issues.append(
            _issue(
                "error",
                "cds_invalid_symbols",
                f"Unsupported nucleotide symbols: {''.join(invalid)}",
                protein_id,
            )
        )
    if not compact:
        issues.append(_issue("error", "cds_empty", "Nucleotide sequence is empty", protein_id))
    return compact, issues


def _fraction(sequence: str, selected: frozenset[str]) -> float:
    return round(sum(character in selected for character in sequence) / len(sequence), 6) if sequence else 0.0


def _gc_fraction(sequence: str) -> float:
    return round(sum(base in "GC" for base in sequence) / len(sequence), 6) if sequence else 0.0


def _longest_homopolymer(sequence: str) -> int:
    longest = current = 0
    previous = None
    for character in sequence:
        current = current + 1 if character == previous else 1
        longest = max(longest, current)
        previous = character
    return longest


def _sequence_entropy(sequence: str) -> float:
    if not sequence:
        return 0.0
    counts = Counter(sequence)
    return round(-sum((count / len(sequence)) * math.log2(count / len(sequence)) for count in counts.values()), 6)


def _first_mismatch(expected: str, observed: str) -> str:
    for index, (expected_aa, observed_aa) in enumerate(zip(expected, observed), start=1):
        if expected_aa != observed_aa:
            return f"position {index}: AA FASTA={expected_aa}, CDS translation={observed_aa}"
    return f"length differs: AA FASTA={len(expected)}, CDS translation={len(observed)}"


def translate_cds(cds: str, protein_id: str) -> tuple[str | None, list[QCIssue], bool]:
    issues: list[QCIssue] = []
    if not cds or any(base not in VALID_NUCLEOTIDES for base in cds):
        return None, issues, False
    if len(cds) % 3:
        issues.append(
            _issue("error", "cds_frame", f"CDS length {len(cds)} is not divisible by 3", protein_id)
        )
        return None, issues, False

    translated: list[str] = []
    ambiguous_codons = 0
    for offset in range(0, len(cds), 3):
        codon = cds[offset : offset + 3]
        if "N" in codon:
            translated.append("X")
            ambiguous_codons += 1
        else:
            translated.append(CODON_TABLE[codon])

    if ambiguous_codons:
        issues.append(
            _issue(
                "warning",
                "cds_ambiguous_codons",
                f"Translated {ambiguous_codons} ambiguous codon(s) as X",
                protein_id,
            )
        )
    terminal_stop = bool(translated and translated[-1] == "*")
    if "*" in translated[:-1]:
        positions = [str(index + 1) for index, amino_acid in enumerate(translated[:-1]) if amino_acid == "*"]
        issues.append(
            _issue("error", "cds_internal_stop", f"Internal stop codon at amino-acid position(s) {', '.join(positions)}", protein_id)
        )
    if terminal_stop:
        translated = translated[:-1]
    else:
        issues.append(_issue("warning", "cds_no_terminal_stop", "CDS has no terminal stop codon", protein_id))
    if cds[:3] != "ATG":
        issues.append(_issue("warning", "cds_non_atg_start", f"CDS starts with {cds[:3] or 'empty'}, not ATG", protein_id))
    return "".join(translated), issues, terminal_stop


def _metrics(aa: str, cds: str, terminal_stop: bool, translation_matches: bool | None) -> dict[str, object]:
    composition = {amino_acid: aa.count(amino_acid) for amino_acid in sorted(CANONICAL_AMINO_ACIDS)}
    estimated_mass = sum(RESIDUE_MASS.get(amino_acid, 0.0) for amino_acid in aa)
    if aa:
        estimated_mass += 18.0153
    codons = [cds[offset : offset + 3] for offset in range(0, len(cds) - 2, 3)]
    return {
        "aa_length": len(aa),
        "cds_length_nt": len(cds),
        "codon_count": len(codons),
        "start_codon": cds[:3] if len(cds) >= 3 else "",
        "terminal_stop_codon": terminal_stop,
        "translation_matches": translation_matches,
        "estimated_molecular_weight_da": round(estimated_mass, 3),
        "hydrophobic_fraction": _fraction(aa, HYDROPHOBIC_AMINO_ACIDS),
        "charged_fraction": _fraction(aa, CHARGED_AMINO_ACIDS),
        "cysteine_count": aa.count("C"),
        "proline_count": aa.count("P"),
        "aa_entropy_bits": _sequence_entropy(aa),
        "aa_longest_homopolymer": _longest_homopolymer(aa),
        "gc_fraction": _gc_fraction(cds),
        "gc1_fraction": _gc_fraction(cds[0::3]),
        "gc2_fraction": _gc_fraction(cds[1::3]),
        "gc3_fraction": _gc_fraction(cds[2::3]),
        "nt_longest_homopolymer": _longest_homopolymer(cds),
        "amino_acid_composition": composition,
    }


def analyze_sequence_pairs(
    amino_acids: list[FastaRecord],
    nucleotides: list[FastaRecord],
    expected_count: int,
) -> tuple[list[ProteinAnalysis], list[QCIssue]]:
    project_issues: list[QCIssue] = []
    if len(amino_acids) != expected_count:
        project_issues.append(
            QCIssue("error", "aa_record_count", f"Expected {expected_count} amino-acid records, found {len(amino_acids)}")
        )
    if len(nucleotides) != expected_count:
        project_issues.append(
            QCIssue("error", "cds_record_count", f"Expected {expected_count} nucleotide records, found {len(nucleotides)}")
        )

    aa_by_id = {record.record_id: record for record in amino_acids}
    cds_by_id = {record.record_id: record for record in nucleotides}
    missing_cds = sorted(aa_by_id.keys() - cds_by_id.keys())
    missing_aa = sorted(cds_by_id.keys() - aa_by_id.keys())
    if missing_cds:
        project_issues.append(QCIssue("error", "missing_cds", f"Missing CDS for: {', '.join(missing_cds)}"))
    if missing_aa:
        project_issues.append(QCIssue("error", "missing_aa", f"Missing amino-acid sequence for: {', '.join(missing_aa)}"))

    proteins: list[ProteinAnalysis] = []
    for protein_id in sorted(aa_by_id.keys() & cds_by_id.keys()):
        aa, aa_issues = normalize_amino_acid(aa_by_id[protein_id].sequence, protein_id)
        cds, cds_issues = normalize_nucleotide(cds_by_id[protein_id].sequence, protein_id)
        translated, translation_issues, terminal_stop = translate_cds(cds, protein_id)
        issues = aa_issues + cds_issues + translation_issues
        translation_matches: bool | None = None
        if translated is not None:
            translation_matches = aa == translated
            if not translation_matches:
                issues.append(
                    _issue(
                        "error",
                        "translation_mismatch",
                        _first_mismatch(aa, translated),
                        protein_id,
                    )
                )
        digest = hashlib.sha256(f"{protein_id}\0{aa}\0{cds}".encode("utf-8")).hexdigest()
        proteins.append(
            ProteinAnalysis(
                protein_id=protein_id,
                candidate_id=f"original-{digest[:16]}",
                amino_acid_sequence=aa,
                nucleotide_sequence=cds,
                translated_sequence=translated,
                metrics=_metrics(aa, cds, terminal_stop, translation_matches),
                issues=issues,
            )
        )
    return proteins, project_issues
