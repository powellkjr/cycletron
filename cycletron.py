#!/usr/bin/env python3

import argparse
import re
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

MESSAGE_DELAY_MS = 1000


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


def convert_m73_remaining_time(lines: list[str]) -> list[str]:
    default_newline = detect_newline(lines)
    converted_lines: list[str] = []

    for line in lines:
        clean_line = strip_line_ending(line)
        line_ending = get_line_ending(line, default_newline)

        match = M73_PATTERN.match(clean_line)

        if not match:
            converted_lines.append(line)
            continue

        percent_text = match.group("percent")
        minutes_text = match.group("minutes")

        try:
            percent_complete = Decimal(percent_text)
            minutes_remaining = Decimal(minutes_text)
        except InvalidOperation:
            converted_lines.append(line)
            continue

        seconds_remaining = int(minutes_remaining * Decimal(60))

        if line.endswith(("\r\n", "\n")):
            converted_lines.append(line)
        else:
            converted_lines.append(f"{line}{line_ending}")

        converted_lines.append(
            f";REMAINING_TIME: {seconds_remaining}{line_ending}"
        )

        if percent_complete >= 100:
            converted_lines.append(
                f";PRINTING_TIME: UNKNOWN{line_ending}"
            )
            continue

        if percent_complete < 0:
            converted_lines.append(
                f";PRINTING_TIME: UNKNOWN{line_ending}"
            )
            continue

        printing_time_seconds = int(
            (Decimal(seconds_remaining) * percent_complete)
            / (Decimal(100) - percent_complete)
        )

        converted_lines.append(
            f";PRINTING_TIME: {printing_time_seconds}{line_ending}"
        )

    return converted_lines


def process_file_in_place(file_path: Path) -> None:
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

    expanded_lines = expand_cycletron_blocks(lines)
    output_lines = convert_m73_remaining_time(expanded_lines)

    file_path.write_text("".join(output_lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Expand Cycletron-marked sections and add remaining-time comments."
    )

    parser.add_argument(
        "file",
        type=Path,
        help="Path to the G-code file to modify in place.",
    )

    args = parser.parse_args()

    try:
        process_file_in_place(args.file)
    except CycletronError as error:
        print(f"Cycletron error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"File error: {error}", file=sys.stderr)
        return 1

    print(f"Cycletron processing complete: {args.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())