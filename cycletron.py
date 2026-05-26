#!/usr/bin/env python3

import argparse
import base64
import binascii
import re
import struct
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path


COUNT_PATTERN = re.compile(
    r"^@CYCLETRON_COUNT\s*(?:=|:)?\s*(\d+)(?:\s*;.*)?\s*$",
    re.IGNORECASE,
)

COUNT_MARKER_PATTERN = re.compile(
    r"^@CYCLETRON_COUNT\b",
    re.IGNORECASE,
)

START_MARKER_PATTERN = re.compile(
    r"^@CYCLETRON_START\s*(?:;.*)?\s*$",
    re.IGNORECASE,
)

END_MARKER_PATTERN = re.compile(
    r"^@CYCLETRON_END\s*(?:;.*)?\s*$",
    re.IGNORECASE,
)

M73_PATTERN = re.compile(
    r"^M73\s+P(?P<percent>\d+(?:\.\d+)?)\s+R(?P<minutes>\d+(?:\.\d+)?)(?:\s*;.*)?\s*$",
    re.IGNORECASE,
)

ESTIMATED_PRINT_TIME_PATTERN = re.compile(
    r"^;\s*estimated printing time \(normal mode\)\s*=\s*(?P<duration>.+?)\s*$",
    re.IGNORECASE,
)

DURATION_TOKEN_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)(?P<unit>[dhms])",
    re.IGNORECASE,
)

THUMBNAIL_BEGIN_PATTERN = re.compile(
    r"^;\s*thumbnail begin\s+(?P<width>\d+)x(?P<height>\d+)\s+(?P<size>\d+)\s*$",
    re.IGNORECASE,
)

THUMBNAIL_END_PATTERN = re.compile(
    r"^;\s*thumbnail end\s*$",
    re.IGNORECASE,
)

SLICER_VARIABLE_PATTERN = re.compile(
    r"^;\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*?)\s*$"
)

VARIABLE_REFERENCE_PATTERN = re.compile(
    r"\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}"
)

INVALID_FILENAME_CHARS_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1F]')

MESSAGE_DELAY_MS = 1000

IDEAMAKER_TEMPLATE_FILENAME = "ideamaker-template.data"
IDEAMAKER_HEADER = b"IDEA - PRINTDATA"

IDEAMAKER_XOR_KEY = bytes.fromhex(
    "e93f2d3d81a3917dfff1201eae0567eb"
    "84b75a370ca87c3f1d00e9d54d4a7b14"
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CycletronError(Exception):
    pass


def detect_newline(lines: list[str]) -> str:
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"

        if line.endswith("\n"):
            return "\n"

    return "\n"


def get_line_ending(line: str, default_newline: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"

    if line.endswith("\n"):
        return "\n"

    return default_newline


def strip_line_ending(line: str) -> str:
    return line.rstrip("\r\n")


def is_start_marker(line: str) -> bool:
    return bool(START_MARKER_PATTERN.match(strip_line_ending(line)))


def is_end_marker(line: str) -> bool:
    return bool(END_MARKER_PATTERN.match(strip_line_ending(line)))


def is_m73_line(line: str) -> bool:
    return bool(M73_PATTERN.match(strip_line_ending(line)))


def parse_duration_to_seconds(duration_text: str) -> int:
    unit_multipliers = {
        "d": Decimal(86400),
        "h": Decimal(3600),
        "m": Decimal(60),
        "s": Decimal(1),
    }

    total_seconds = Decimal(0)
    matched_any_token = False

    for match in DURATION_TOKEN_PATTERN.finditer(duration_text):
        matched_any_token = True

        value_text = match.group("value")
        unit = match.group("unit").lower()

        try:
            value = Decimal(value_text)
        except InvalidOperation:
            raise CycletronError(
                f"Invalid estimated printing time value: {value_text}"
            )

        total_seconds += value * unit_multipliers[unit]

    leftover_text = DURATION_TOKEN_PATTERN.sub("", duration_text).strip()

    if not matched_any_token or leftover_text:
        raise CycletronError(
            f"Could not parse estimated printing time duration: {duration_text}"
        )

    return int(total_seconds)


def format_print_time_for_filename(total_seconds: int) -> str:
    if total_seconds < 0:
        total_seconds = 0

    rounded_minutes = (total_seconds + 30) // 60
    hours = rounded_minutes // 60
    minutes = rounded_minutes % 60

    if hours > 0:
        return f"{hours}h{minutes}m"

    return f"{minutes}m"


def find_estimated_print_time_seconds(lines: list[str]) -> int | None:
    matches: list[tuple[int, int]] = []

    for index, line in enumerate(lines):
        clean_line = strip_line_ending(line)
        match = ESTIMATED_PRINT_TIME_PATTERN.match(clean_line)

        if not match:
            continue

        duration_text = match.group("duration")
        total_seconds = parse_duration_to_seconds(duration_text)
        matches.append((total_seconds, index + 1))

    if not matches:
        return None

    unique_totals = {total_seconds for total_seconds, _ in matches}

    if len(unique_totals) > 1:
        line_numbers = ", ".join(str(line_number) for _, line_number in matches)
        raise CycletronError(
            "Found multiple conflicting estimated printing time comments "
            f"on lines {line_numbers}."
        )

    return matches[0][0]


def find_global_count(lines: list[str]) -> tuple[int, int]:
    count_matches: list[tuple[int, int]] = []

    for index, line in enumerate(lines):
        clean_line = strip_line_ending(line)

        match = COUNT_PATTERN.match(clean_line)

        if match:
            count_matches.append((int(match.group(1)), index))
        elif COUNT_MARKER_PATTERN.match(clean_line):
            raise CycletronError(
                f"Found @CYCLETRON_COUNT on line {index + 1}, "
                "but no valid number was found."
            )

    if not count_matches:
        raise CycletronError("Missing @CYCLETRON_COUNT value.")

    if len(count_matches) > 1:
        raise CycletronError(
            "Found multiple @CYCLETRON_COUNT values. Only one global count is allowed."
        )

    count, count_line_index = count_matches[0]

    if count < 1:
        raise CycletronError("@CYCLETRON_COUNT must be 1 or greater.")

    return count, count_line_index


def validate_markers(lines: list[str]) -> None:
    start_count = sum(1 for line in lines if is_start_marker(line))
    end_count = sum(1 for line in lines if is_end_marker(line))

    if start_count == 0:
        raise CycletronError("Missing @CYCLETRON_START marker.")

    if end_count == 0:
        raise CycletronError("Missing @CYCLETRON_END marker.")

    if start_count != end_count:
        raise CycletronError(
            f"Mismatched Cycletron markers. Found {start_count} start marker(s) "
            f"and {end_count} end marker(s)."
        )


def expand_cycletron_blocks(lines: list[str]) -> list[str]:
    count, count_line_index = find_global_count(lines)
    validate_markers(lines)

    newline = detect_newline(lines)
    output_lines: list[str] = []

    index = 0
    block_number = 0

    while index < len(lines):
        line = lines[index]

        if index == count_line_index:
            output_lines.append(
                f"; Processed by Cycletron: repeated each marked section {count} times{newline}"
            )
            index += 1
            continue

        if is_end_marker(line):
            raise CycletronError(
                f"Found @CYCLETRON_END before @CYCLETRON_START on line {index + 1}."
            )

        if is_start_marker(line):
            block_number += 1
            start_line_number = index + 1

            output_lines.append(line)
            index += 1

            block_lines: list[str] = []

            while index < len(lines):
                current_line = lines[index]

                if is_start_marker(current_line):
                    raise CycletronError(
                        f"Nested @CYCLETRON_START found on line {index + 1}. "
                        "Nested blocks are not supported."
                    )

                if is_end_marker(current_line):
                    break

                block_lines.append(current_line)
                index += 1

            if index >= len(lines):
                raise CycletronError(
                    f"Missing @CYCLETRON_END for block starting on line "
                    f"{start_line_number}."
                )

            if not block_lines:
                raise CycletronError(
                    f"No G-code found between markers for block starting on line "
                    f"{start_line_number}."
                )

            for cycle in range(1, count + 1):
                output_lines.append(f"M117 Running cycle {cycle}/{count}{newline}")
                output_lines.append(f"G4 P{MESSAGE_DELAY_MS}{newline}")
                output_lines.extend(block_lines)

            output_lines.append(lines[index])
            index += 1
            continue

        output_lines.append(line)
        index += 1

    if block_number == 0:
        raise CycletronError("No Cycletron blocks were processed.")

    return output_lines


def add_m73_time_comments(
    lines: list[str],
    total_print_seconds: int,
) -> list[str]:
    default_newline = detect_newline(lines)
    converted_lines: list[str] = []

    for line in lines:
        clean_line = strip_line_ending(line)
        line_ending = get_line_ending(line, default_newline)

        match = M73_PATTERN.match(clean_line)

        if not match:
            converted_lines.append(line)
            continue

        minutes_text = match.group("minutes")

        try:
            minutes_remaining = Decimal(minutes_text)
        except InvalidOperation:
            converted_lines.append(line)
            continue

        seconds_remaining = int(minutes_remaining * Decimal(60))
        printing_time_seconds = total_print_seconds - seconds_remaining

        if printing_time_seconds < 0:
            printing_time_seconds = 0

        if line.endswith(("\r\n", "\n")):
            converted_lines.append(line)
        else:
            converted_lines.append(f"{line}{line_ending}")

        converted_lines.append(
            f";REMAINING_TIME: {seconds_remaining}{line_ending}"
        )
        converted_lines.append(
            f";PRINTING_TIME: {printing_time_seconds}{line_ending}"
        )

    return converted_lines


def extract_largest_thumbnail_png(lines: list[str]) -> bytes | None:
    candidates: list[tuple[int, bytes]] = []
    index = 0

    while index < len(lines):
        clean_line = strip_line_ending(lines[index])
        begin_match = THUMBNAIL_BEGIN_PATTERN.match(clean_line)

        if not begin_match:
            index += 1
            continue

        width = int(begin_match.group("width"))
        height = int(begin_match.group("height"))
        declared_size = int(begin_match.group("size"))
        area = width * height

        index += 1
        encoded_parts: list[str] = []

        while index < len(lines):
            current_clean = strip_line_ending(lines[index])

            if THUMBNAIL_END_PATTERN.match(current_clean):
                break

            if not current_clean.startswith(";"):
                raise CycletronError(
                    f"Malformed thumbnail block near line {index + 1}."
                )

            payload = current_clean[1:].strip()

            if payload:
                encoded_parts.append(payload)

            index += 1

        if index >= len(lines):
            raise CycletronError("Thumbnail block started but was not closed.")

        encoded_data = "".join(encoded_parts)

        if not encoded_data:
            raise CycletronError("Thumbnail block was empty.")

        if declared_size != len(encoded_data):
            raise CycletronError(
                "Thumbnail block size does not match the declared encoded length."
            )

        try:
            png_bytes = base64.b64decode(encoded_data, validate=True)
        except (binascii.Error, ValueError) as error:
            raise CycletronError(f"Failed to decode thumbnail data: {error}")

        candidates.append((area, png_bytes))
        index += 1

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def parse_slicer_variables(lines: list[str], file_path: Path) -> dict[str, str]:
    variables: dict[str, str] = {}

    for line in lines:
        clean_line = strip_line_ending(line)
        match = SLICER_VARIABLE_PATTERN.match(clean_line)

        if not match:
            continue

        name = match.group("name").strip()
        value = match.group("value").strip()
        variables[name] = value

    estimated_print_seconds = find_estimated_print_time_seconds(lines)

    if estimated_print_seconds is not None:
        variables.setdefault(
            "print_time",
            format_print_time_for_filename(estimated_print_seconds),
        )

    variables.setdefault("input_filename", file_path.name)
    variables.setdefault("input_filename_base", file_path.stem)
    variables.setdefault("input_filepath", str(file_path))
    variables.setdefault("input_dir", str(file_path.parent))

    return variables


def resolve_variable_references(
    template: str,
    variables: dict[str, str],
    max_depth: int = 20,
) -> str:
    resolved = template

    for _ in range(max_depth):
        changed = False

        def replace_match(match: re.Match[str]) -> str:
            nonlocal changed

            variable_name = match.group("name")

            if variable_name not in variables:
                return match.group(0)

            changed = True
            return variables[variable_name]

        next_resolved = VARIABLE_REFERENCE_PATTERN.sub(replace_match, resolved)

        if not changed or next_resolved == resolved:
            return next_resolved

        resolved = next_resolved

    return resolved


def sanitize_filename(filename: str) -> str:
    sanitized = INVALID_FILENAME_CHARS_PATTERN.sub("_", filename)
    sanitized = sanitized.strip().rstrip(".")

    if not sanitized:
        return "thumbnail.png"

    return sanitized


def build_suggested_output_filename(
    lines: list[str],
    file_path: Path,
    suffix: str,
) -> str:
    variables = parse_slicer_variables(lines, file_path)
    output_filename_format = variables.get("output_filename_format")

    if output_filename_format:
        resolved_filename = resolve_variable_references(
            output_filename_format,
            variables,
        )

        suggested_name = Path(resolved_filename).with_suffix(suffix).name
    else:
        suggested_name = file_path.with_suffix(suffix).name

    return sanitize_filename(suggested_name)


def ask_user_for_thumbnail_save_path(
    file_path: Path,
    suggested_filename: str,
) -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as error:
        raise CycletronError(
            "Could not open save dialog because tkinter is not available."
        ) from error

    root = tk.Tk()
    root.withdraw()
    root.update()

    try:
        selected_path_text = filedialog.asksaveasfilename(
            title="Save G-code Thumbnail",
            initialdir=str(file_path.parent),
            initialfile=suggested_filename,
            defaultextension=".png",
            filetypes=[
                ("PNG image", "*.png"),
                ("All files", "*.*"),
            ],
        )
    finally:
        root.destroy()

    if not selected_path_text:
        return None

    selected_path = Path(selected_path_text)

    if selected_path.suffix.lower() != ".png":
        selected_path = selected_path.with_suffix(".png")

    return selected_path


def write_thumbnail_png_with_dialog(
    file_path: Path,
    lines: list[str],
    png_bytes: bytes,
) -> Path | None:
    suggested_filename = build_suggested_output_filename(
        lines=lines,
        file_path=file_path,
        suffix=".png",
    )

    output_path = ask_user_for_thumbnail_save_path(
        file_path=file_path,
        suggested_filename=suggested_filename,
    )

    if output_path is None:
        return None

    output_path.write_bytes(png_bytes)
    return output_path


def xor_ideamaker_payload(payload: bytes) -> bytes:
    return bytes(
        byte ^ IDEAMAKER_XOR_KEY[index % len(IDEAMAKER_XOR_KEY)]
        for index, byte in enumerate(payload)
    )


def find_png_ranges(data: bytes) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    search_start = 0

    while True:
        png_start = data.find(PNG_SIGNATURE, search_start)

        if png_start == -1:
            break

        cursor = png_start + len(PNG_SIGNATURE)

        try:
            while True:
                if cursor + 8 > len(data):
                    raise CycletronError(
                        "PNG ended unexpectedly while scanning ideaMaker data."
                    )

                chunk_length = struct.unpack(">I", data[cursor:cursor + 4])[0]
                chunk_type = data[cursor + 4:cursor + 8]

                cursor += 8 + chunk_length + 4

                if cursor > len(data):
                    raise CycletronError(
                        "PNG chunk extends past end of decoded ideaMaker payload."
                    )

                if chunk_type == b"IEND":
                    ranges.append((png_start, cursor))
                    search_start = cursor
                    break

        except CycletronError:
            search_start = png_start + 1

    return ranges


def find_local_ideamaker_template_path(file_path: Path) -> Path | None:
    candidate_paths: list[Path] = []

    try:
        script_dir = Path(__file__).resolve().parent
        candidate_paths.append(script_dir / IDEAMAKER_TEMPLATE_FILENAME)
    except NameError:
        pass

    candidate_paths.append(Path.cwd() / IDEAMAKER_TEMPLATE_FILENAME)
    candidate_paths.append(file_path.parent / IDEAMAKER_TEMPLATE_FILENAME)

    seen: set[Path] = set()

    for candidate in candidate_paths:
        resolved_candidate = candidate.resolve()

        if resolved_candidate in seen:
            continue

        seen.add(resolved_candidate)

        if resolved_candidate.exists() and resolved_candidate.is_file():
            return resolved_candidate

    return None


def patch_ideamaker_data_template(
    template_data_path: Path,
    output_data_path: Path,
    replacement_png_bytes: bytes,
) -> None:
    raw = template_data_path.read_bytes()

    if not raw.startswith(IDEAMAKER_HEADER):
        raise CycletronError(
            f"Template file is not an ideaMaker PRINTDATA file: {template_data_path}"
        )

    encoded_payload = raw[len(IDEAMAKER_HEADER):]
    decoded_payload = xor_ideamaker_payload(encoded_payload)

    png_ranges = find_png_ranges(decoded_payload)

    if not png_ranges:
        raise CycletronError(
            f"No embedded PNGs found in ideaMaker template: {template_data_path}"
        )

    rebuilt_parts: list[bytes] = []
    last_end = 0

    for start, end in png_ranges:
        rebuilt_parts.append(decoded_payload[last_end:start])
        rebuilt_parts.append(replacement_png_bytes)
        last_end = end

    rebuilt_parts.append(decoded_payload[last_end:])

    rebuilt_decoded_payload = b"".join(rebuilt_parts)
    rebuilt_encoded_payload = xor_ideamaker_payload(rebuilt_decoded_payload)

    output_data_path.write_bytes(IDEAMAKER_HEADER + rebuilt_encoded_payload)


def write_ideamaker_data_if_template_exists(
    file_path: Path,
    png_output_path: Path | None,
    replacement_png_bytes: bytes,
) -> Path | None:
    if png_output_path is None:
        return None

    template_data_path = find_local_ideamaker_template_path(file_path)

    if template_data_path is None:
        return None

    output_data_path = png_output_path.with_suffix(".data")

    patch_ideamaker_data_template(
        template_data_path=template_data_path,
        output_data_path=output_data_path,
        replacement_png_bytes=replacement_png_bytes,
    )

    return output_data_path


def process_file_in_place(file_path: Path) -> tuple[Path | None, Path | None]:
    if not file_path.exists():
        raise CycletronError(f"File does not exist: {file_path}")

    if not file_path.is_file():
        raise CycletronError(f"Path is not a file: {file_path}")

    try:
        original_text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise CycletronError(
            "Could not read file as UTF-8. Save the G-code file as UTF-8 and try again."
        )

    lines = original_text.splitlines(keepends=True)

    if not lines:
        raise CycletronError("File is empty.")

    color_thumbnail_png = extract_largest_thumbnail_png(lines)

    expanded_lines = expand_cycletron_blocks(lines)

    if any(is_m73_line(line) for line in expanded_lines):
        total_print_seconds = find_estimated_print_time_seconds(lines)

        if total_print_seconds is None:
            raise CycletronError(
                "Found M73 lines, but missing estimated printing time comment. "
                "Expected a line like: "
                "; estimated printing time (normal mode) = 1h 20m 11s"
            )

        output_lines = add_m73_time_comments(
            expanded_lines,
            total_print_seconds,
        )
    else:
        output_lines = expanded_lines

    file_path.write_text("".join(output_lines), encoding="utf-8")

    thumbnail_output_path = None
    data_output_path = None

    if color_thumbnail_png is not None:
        thumbnail_output_path = write_thumbnail_png_with_dialog(
            file_path=file_path,
            lines=lines,
            png_bytes=color_thumbnail_png,
        )

        data_output_path = write_ideamaker_data_if_template_exists(
            file_path=file_path,
            png_output_path=thumbnail_output_path,
            replacement_png_bytes=color_thumbnail_png,
        )

    return thumbnail_output_path, data_output_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Expand Cycletron-marked sections, add print-time comments, "
            "extract an embedded thumbnail PNG, and optionally generate "
            "an ideaMaker DATA file from ideamaker-template.data."
        )
    )

    parser.add_argument(
        "file",
        type=Path,
        help="Path to the G-code file to modify in place.",
    )

    args = parser.parse_args()

    try:
        thumbnail_output_path, data_output_path = process_file_in_place(args.file)
    except CycletronError as error:
        print(f"Cycletron error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"File error: {error}", file=sys.stderr)
        return 1

    print(f"Cycletron processing complete: {args.file}")

    if thumbnail_output_path is not None:
        print(f"Thumbnail saved to: {thumbnail_output_path}")
    else:
        print("No thumbnail PNG was saved.")

    if data_output_path is not None:
        print(f"ideaMaker DATA saved to: {data_output_path}")
    else:
        print(
            "No ideaMaker DATA file was saved. "
            f"Place {IDEAMAKER_TEMPLATE_FILENAME} next to the script to enable it."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())