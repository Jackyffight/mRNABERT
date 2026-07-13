"""Deterministic residue, confidence, and geometry metrics for Stage 3 PDB files."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any


THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
}
RULESET_ID = "structure-exploratory-rules-v1"


@dataclass(frozen=True)
class ResidueGeometry:
    chain_id: str
    residue_number: int
    insertion_code: str
    amino_acid: str
    ca: tuple[float, float, float]
    raw_b_factor: float
    plddt: float


@dataclass(frozen=True)
class ParsedStructure:
    residues: tuple[ResidueGeometry, ...]
    plddt_scale: str

    @property
    def sequence(self) -> str:
        return "".join(residue.amino_acid for residue in self.residues)


def _finite_float(value: str, label: str, line_number: int) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise ValueError(f"Invalid {label} at PDB line {line_number}: {value!r}") from error
    if not math.isfinite(result):
        raise ValueError(f"Non-finite {label} at PDB line {line_number}")
    return result


def parse_ca_pdb(path: str | Path) -> ParsedStructure:
    pdb_path = Path(path)
    raw_residues: list[tuple[str, int, str, str, tuple[float, float, float], float]] = []
    seen: set[tuple[str, int, str]] = set()
    for line_number, line in enumerate(pdb_path.read_text(encoding="ascii").splitlines(), 1):
        if not line.startswith("ATOM") or line[12:16].strip() != "CA":
            continue
        if len(line) < 66:
            raise ValueError(f"Truncated C-alpha PDB record at line {line_number}")
        alternate = line[16:17]
        if alternate not in {" ", "A"}:
            continue
        residue_name = line[17:20].strip().upper()
        try:
            amino_acid = THREE_TO_ONE[residue_name]
        except KeyError as error:
            raise ValueError(
                f"Unsupported residue {residue_name!r} at PDB line {line_number}"
            ) from error
        chain_id = line[21:22].strip() or "_"
        try:
            residue_number = int(line[22:26])
        except ValueError as error:
            raise ValueError(f"Invalid residue number at PDB line {line_number}") from error
        insertion_code = line[26:27].strip()
        identity = (chain_id, residue_number, insertion_code)
        if identity in seen:
            raise ValueError(f"Duplicate C-alpha residue in PDB: {identity}")
        seen.add(identity)
        coordinate = (
            _finite_float(line[30:38], "x coordinate", line_number),
            _finite_float(line[38:46], "y coordinate", line_number),
            _finite_float(line[46:54], "z coordinate", line_number),
        )
        b_factor = _finite_float(line[60:66], "B-factor", line_number)
        raw_residues.append(
            (chain_id, residue_number, insertion_code, amino_acid, coordinate, b_factor)
        )
    if not raw_residues:
        raise ValueError(f"PDB has no C-alpha residues: {pdb_path}")
    chains = {record[0] for record in raw_residues}
    if len(chains) != 1:
        raise ValueError(f"Expected one predicted chain, observed: {sorted(chains)}")
    raw_values = [record[-1] for record in raw_residues]
    if min(raw_values) < 0:
        raise ValueError("PDB pLDDT/B-factor values must be non-negative")
    if max(raw_values) <= 1.000001:
        scale = "zero_to_one"
        multiplier = 100.0
    elif max(raw_values) <= 100.000001:
        scale = "zero_to_hundred"
        multiplier = 1.0
    else:
        raise ValueError("PDB pLDDT/B-factor values exceed 100")
    residues = tuple(
        ResidueGeometry(
            chain_id=record[0],
            residue_number=record[1],
            insertion_code=record[2],
            amino_acid=record[3],
            ca=record[4],
            raw_b_factor=record[5],
            plddt=record[5] * multiplier,
        )
        for record in raw_residues
    )
    return ParsedStructure(residues=residues, plddt_scale=scale)


def _distance(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)))


def _centroid(coordinates: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    count = len(coordinates)
    return tuple(sum(point[axis] for point in coordinates) / count for axis in range(3))


def _principal_axes(
    coordinates: list[tuple[float, float, float]],
) -> tuple[list[float], list[list[float]], list[float]]:
    center = _centroid(coordinates)
    centered = [tuple(point[axis] - center[axis] for axis in range(3)) for point in coordinates]
    count = len(centered)
    covariance = [
        [
            sum(point[row] * point[column] for point in centered) / count
            for column in range(3)
        ]
        for row in range(3)
    ]
    vectors = [[1.0 if row == column else 0.0 for column in range(3)] for row in range(3)]
    for _ in range(64):
        off_diagonal = [
            (abs(covariance[0][1]), 0, 1),
            (abs(covariance[0][2]), 0, 2),
            (abs(covariance[1][2]), 1, 2),
        ]
        magnitude, p, q = max(off_diagonal)
        if magnitude < 1e-12:
            break
        angle = 0.5 * math.atan2(
            2.0 * covariance[p][q],
            covariance[q][q] - covariance[p][p],
        )
        cosine, sine = math.cos(angle), math.sin(angle)
        for index in range(3):
            if index in {p, q}:
                continue
            left = covariance[index][p]
            right = covariance[index][q]
            covariance[index][p] = covariance[p][index] = cosine * left - sine * right
            covariance[index][q] = covariance[q][index] = sine * left + cosine * right
        app, aqq, apq = covariance[p][p], covariance[q][q], covariance[p][q]
        covariance[p][p] = cosine * cosine * app - 2 * sine * cosine * apq + sine * sine * aqq
        covariance[q][q] = sine * sine * app + 2 * sine * cosine * apq + cosine * cosine * aqq
        covariance[p][q] = covariance[q][p] = 0.0
        for row in range(3):
            left = vectors[row][p]
            right = vectors[row][q]
            vectors[row][p] = cosine * left - sine * right
            vectors[row][q] = sine * left + cosine * right
    eigenpairs = []
    for column in range(3):
        vector = [vectors[row][column] for row in range(3)]
        norm = math.sqrt(sum(value * value for value in vector))
        vector = [value / norm for value in vector]
        largest_index = max(range(3), key=lambda index: abs(vector[index]))
        if vector[largest_index] < 0:
            vector = [-value for value in vector]
        eigenpairs.append((max(0.0, covariance[column][column]), vector))
    eigenpairs.sort(key=lambda pair: pair[0], reverse=True)
    eigenvalues = [pair[0] for pair in eigenpairs]
    axes = [pair[1] for pair in eigenpairs]
    extents = []
    for axis in axes:
        projections = [sum(point[index] * axis[index] for index in range(3)) for point in centered]
        extents.append(max(projections) - min(projections))
    return eigenvalues, axes, extents


def geometry_metrics(residues: tuple[ResidueGeometry, ...] | list[ResidueGeometry]) -> dict[str, Any]:
    if not residues:
        raise ValueError("Cannot compute geometry for an empty residue range")
    coordinates = [residue.ca for residue in residues]
    center = _centroid(coordinates)
    radius_of_gyration = math.sqrt(
        sum(_distance(point, center) ** 2 for point in coordinates) / len(coordinates)
    )
    eigenvalues, axes, extents = _principal_axes(coordinates)
    total = sum(eigenvalues)
    anisotropy = (
        1.0
        - 3.0
        * (
            eigenvalues[0] * eigenvalues[1]
            + eigenvalues[1] * eigenvalues[2]
            + eigenvalues[2] * eigenvalues[0]
        )
        / (total * total)
        if total > 0
        else 0.0
    )
    max_distance = 0.0
    clash_count = 0
    for left_index, left in enumerate(coordinates):
        for right_index in range(left_index + 1, len(coordinates)):
            separation = _distance(left, coordinates[right_index])
            max_distance = max(max_distance, separation)
            if right_index - left_index > 2 and separation < 3.0:
                clash_count += 1
    return {
        "centroid_angstrom": [round(value, 6) for value in center],
        "radius_of_gyration_angstrom": round(radius_of_gyration, 6),
        "end_to_end_distance_angstrom": round(
            _distance(coordinates[0], coordinates[-1]), 6
        ),
        "maximum_ca_distance_angstrom": round(max_distance, 6),
        "principal_axis_variances": [round(value, 6) for value in eigenvalues],
        "principal_axis_vectors": [
            [round(value, 8) for value in axis] for axis in axes
        ],
        "principal_axis_extents_angstrom": [round(value, 6) for value in extents],
        "shape_anisotropy": round(max(0.0, min(1.0, anisotropy)), 8),
        "nonlocal_ca_clash_count": clash_count,
    }


def _low_confidence_segments(residues: tuple[ResidueGeometry, ...], threshold: float = 70.0) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    start: int | None = None
    for index, residue in enumerate(residues, 1):
        if residue.plddt < threshold and start is None:
            start = index
        if residue.plddt >= threshold and start is not None:
            selected = residues[start - 1 : index - 1]
            segments.append(
                {
                    "start": start,
                    "end": index - 1,
                    "length": len(selected),
                    "mean_plddt": round(sum(item.plddt for item in selected) / len(selected), 4),
                }
            )
            start = None
    if start is not None:
        selected = residues[start - 1 :]
        segments.append(
            {
                "start": start,
                "end": len(residues),
                "length": len(selected),
                "mean_plddt": round(sum(item.plddt for item in selected) / len(selected), 4),
            }
        )
    return segments


def normalize_result_plddt(value: Any) -> tuple[float, str]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("ESMFold2 mean_plddt must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError("ESMFold2 mean_plddt must be finite and non-negative")
    if numeric <= 1.000001:
        return numeric * 100.0, "zero_to_one"
    if numeric <= 100.000001:
        return numeric, "zero_to_hundred"
    raise ValueError("ESMFold2 mean_plddt exceeds 100")


def assess_candidate_structure(
    candidate: dict[str, Any],
    parsed: ParsedStructure,
    result: dict[str, Any],
) -> dict[str, Any]:
    sequence = candidate["amino_acid_sequence"]
    if parsed.sequence != sequence:
        raise ValueError(
            f"PDB sequence mismatch for {candidate['candidate_id']}: "
            f"expected={len(sequence)} observed={len(parsed.sequence)}"
        )
    metrics = result.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError(f"Missing ESMFold2 metrics for {candidate['candidate_id']}")
    result_plddt, result_scale = normalize_result_plddt(metrics.get("mean_plddt"))
    ptm = metrics.get("ptm")
    if (
        not isinstance(ptm, (int, float))
        or isinstance(ptm, bool)
        or not math.isfinite(float(ptm))
        or not 0.0 <= float(ptm) <= 1.0
    ):
        raise ValueError(f"Invalid pTM for {candidate['candidate_id']}: {ptm}")
    residue_plddt = [residue.plddt for residue in parsed.residues]
    mean_plddt = sum(residue_plddt) / len(residue_plddt)
    low_segments = _low_confidence_segments(parsed.residues)
    components = []
    for index, component in enumerate(candidate["inferred_components"], 1):
        start, end = int(component["candidate_start"]), int(component["candidate_end"])
        if start < 1 or end < start or end > len(parsed.residues):
            raise ValueError(
                f"Invalid component range for {candidate['candidate_id']}: {start}-{end}"
            )
        selected = parsed.residues[start - 1 : end]
        component_plddt = [residue.plddt for residue in selected]
        components.append(
            {
                "component_index": index,
                "component_type": component["component_type"],
                "candidate_start": start,
                "candidate_end": end,
                "source_protein_id": component.get("source_protein_id"),
                "source_start": component.get("source_start"),
                "source_end": component.get("source_end"),
                "sequence_sha256": component["sequence_sha256"],
                "mean_plddt": round(sum(component_plddt) / len(component_plddt), 4),
                "minimum_plddt": round(min(component_plddt), 4),
                "low_confidence_fraction": round(
                    sum(value < 70.0 for value in component_plddt) / len(component_plddt), 6
                ),
                "geometry": geometry_metrics(selected),
            }
        )
    boundaries = []
    for left, right in zip(components, components[1:]):
        boundary = left["candidate_end"]
        window_start = max(1, boundary - 4)
        window_end = min(len(parsed.residues), boundary + 5)
        window = parsed.residues[window_start - 1 : window_end]
        boundary_plddt = [residue.plddt for residue in window]
        boundaries.append(
            {
                "left_component_index": left["component_index"],
                "right_component_index": right["component_index"],
                "boundary_after_residue": boundary,
                "window_start": window_start,
                "window_end": window_end,
                "window_mean_plddt": round(
                    sum(boundary_plddt) / len(boundary_plddt), 4
                ),
                "window_minimum_plddt": round(min(boundary_plddt), 4),
                "junction_ca_distance_angstrom": round(
                    _distance(parsed.residues[boundary - 1].ca, parsed.residues[boundary].ca),
                    6,
                ),
            }
        )
    geometry = geometry_metrics(parsed.residues)
    flags = []
    if mean_plddt < 70.0:
        flags.append({"code": "mean_plddt_below_70", "severity": "review"})
    if float(ptm) < 0.5:
        flags.append({"code": "ptm_below_0_5", "severity": "review"})
    if any(segment["length"] >= 10 for segment in low_segments):
        flags.append({"code": "extended_low_confidence_segment", "severity": "review"})
    if any(boundary["window_mean_plddt"] < 70.0 for boundary in boundaries):
        flags.append({"code": "low_confidence_component_boundary", "severity": "review"})
    if geometry["nonlocal_ca_clash_count"]:
        flags.append({"code": "nonlocal_ca_clash", "severity": "review"})
    if abs(result_plddt - mean_plddt) > 5.0:
        flags.append({"code": "result_pdb_plddt_disagreement", "severity": "review"})
    if mean_plddt >= 80.0 and float(ptm) >= 0.7:
        confidence_band = "higher_confidence"
    elif mean_plddt >= 70.0 and float(ptm) >= 0.5:
        confidence_band = "mixed_confidence"
    else:
        confidence_band = "low_confidence"
    return {
        "candidate_id": candidate["candidate_id"],
        "candidate_key": candidate["candidate_key"],
        "display_name": candidate["display_name"],
        "candidate_type": candidate["candidate_type"],
        "release_status": candidate["release_status"],
        "sequence_sha256": candidate["amino_acid_sha256"],
        "length": len(sequence),
        "status": "assessed",
        "ruleset_id": RULESET_ID,
        "confidence_band": confidence_band,
        "mean_plddt": round(mean_plddt, 4),
        "minimum_plddt": round(min(residue_plddt), 4),
        "ptm": round(float(ptm), 6),
        "result_mean_plddt": round(result_plddt, 4),
        "result_plddt_scale": result_scale,
        "pdb_plddt_scale": parsed.plddt_scale,
        "fraction_plddt_at_least_90": round(
            sum(value >= 90.0 for value in residue_plddt) / len(residue_plddt), 6
        ),
        "fraction_plddt_below_70": round(
            sum(value < 70.0 for value in residue_plddt) / len(residue_plddt), 6
        ),
        "fraction_plddt_below_50": round(
            sum(value < 50.0 for value in residue_plddt) / len(residue_plddt), 6
        ),
        "low_confidence_segments": low_segments,
        "geometry": geometry,
        "components": components,
        "boundaries": boundaries,
        "source_geometry_comparisons": [],
        "review_flags": flags,
    }


def distance_matrix_rmsd(
    left: list[ResidueGeometry] | tuple[ResidueGeometry, ...],
    right: list[ResidueGeometry] | tuple[ResidueGeometry, ...],
) -> float:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("Distance-matrix RMSD requires equal ranges of at least two residues")
    squared_error = 0.0
    pairs = 0
    for first in range(len(left)):
        for second in range(first + 1, len(left)):
            delta = _distance(left[first].ca, left[second].ca) - _distance(
                right[first].ca, right[second].ca
            )
            squared_error += delta * delta
            pairs += 1
    return math.sqrt(squared_error / pairs)


def add_source_geometry_comparisons(
    candidates: list[dict[str, Any]],
    assessments: list[dict[str, Any]],
    structures: dict[str, ParsedStructure],
) -> None:
    assessment_by_id = {item["candidate_id"]: item for item in assessments}
    source_controls: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if candidate["candidate_type"] != "source_control":
            continue
        components = candidate["inferred_components"]
        if len(components) == 1 and components[0]["component_type"] == "source_segment":
            source_controls[components[0]["source_protein_id"]] = candidate
    for candidate in candidates:
        assessment = assessment_by_id[candidate["candidate_id"]]
        candidate_structure = structures[candidate["candidate_id"]]
        comparisons = []
        for component_index, component in enumerate(candidate["inferred_components"], 1):
            source_protein_id = component.get("source_protein_id")
            if component["component_type"] != "source_segment" or not source_protein_id:
                continue
            source_candidate = source_controls.get(source_protein_id)
            if source_candidate is None or source_candidate["candidate_id"] == candidate["candidate_id"]:
                continue
            candidate_range = candidate_structure.residues[
                int(component["candidate_start"]) - 1 : int(component["candidate_end"])
            ]
            source_structure = structures[source_candidate["candidate_id"]]
            source_range = source_structure.residues[
                int(component["source_start"]) - 1 : int(component["source_end"])
            ]
            if "".join(item.amino_acid for item in candidate_range) != "".join(
                item.amino_acid for item in source_range
            ):
                raise ValueError(
                    f"Source comparison sequence mismatch for {candidate['candidate_id']} "
                    f"component {component_index}"
                )
            comparison = {
                "component_index": component_index,
                "source_protein_id": source_protein_id,
                "source_candidate_id": source_candidate["candidate_id"],
                "source_start": component["source_start"],
                "source_end": component["source_end"],
                "residue_count": len(candidate_range),
                "distance_matrix_rmsd_angstrom": round(
                    distance_matrix_rmsd(candidate_range, source_range), 6
                ),
                "mean_plddt_delta": round(
                    sum(item.plddt for item in candidate_range) / len(candidate_range)
                    - sum(item.plddt for item in source_range) / len(source_range),
                    4,
                ),
            }
            comparisons.append(comparison)
            if comparison["distance_matrix_rmsd_angstrom"] > 3.0:
                assessment["review_flags"].append(
                    {
                        "code": "source_geometry_distance_rmsd_above_3A",
                        "severity": "review",
                        "component_index": component_index,
                    }
                )
        assessment["source_geometry_comparisons"] = comparisons
