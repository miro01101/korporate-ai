from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import re
import unicodedata

DECIMAL_TYPE_PATTERN = re.compile(r"^decimal\((?P<precision>\d+),(?P<scale>\d+)\)$", re.IGNORECASE)
CURRENCY_SUFFIX_PATTERN = re.compile(r"(?:\s*(?:EUR|€))\s*$", re.IGNORECASE)
UNSUPPORTED_CHARACTER_PATTERN = re.compile(r"[^0-9+\-.,'’\s\u00a0\u202f\u2007]")
GROUPING_CHARS = (" ", "\u00a0", "\u202f", "\u2007", "'", "’")


class NumericNormalizationError(ValueError):
    pass


def _strip_grouping(text: str) -> str:
    for character in GROUPING_CHARS:
        text = text.replace(character, "")
    return text


def _decimal_text(text: str) -> str:
    comma_count = text.count(",")
    dot_count = text.count(".")
    comma_position = text.rfind(",")
    dot_position = text.rfind(".")
    decimal_position = max(comma_position, dot_position)
    if decimal_position < 0:
        return text

    decimal_separator = text[decimal_position]
    same_separator_count = comma_count if decimal_separator == "," else dot_count
    other_separator_count = dot_count if decimal_separator == "," else comma_count
    if same_separator_count > 1 and other_separator_count == 0:
        groups = text.split(decimal_separator)
        if not groups or any(not group for group in groups):
            raise NumericNormalizationError(f"Neplatne desatinne cislo: {text!r}")
        if any(len(group) != 3 for group in groups[1:-1]):
            raise NumericNormalizationError(f"Neplatne oddelovace: {text!r}")

    integer_part = re.sub(r"[,.]", "", text[:decimal_position])
    fractional_part = text[decimal_position + 1 :]
    if not integer_part or not fractional_part or not fractional_part.isdigit():
        raise NumericNormalizationError(f"Neplatne desatinne cislo: {text!r}")
    return integer_part + "." + fractional_part


def _integer_text(text: str) -> str:
    if "," not in text and "." not in text:
        return text
    groups = re.split(r"[,.]", text)
    if groups and all(groups) and all(len(group) == 3 for group in groups[1:]):
        return "".join(groups)
    decimal_text = _decimal_text(text)
    number = Decimal(decimal_text)
    if number != number.to_integral_value():
        raise NumericNormalizationError(f"Hodnota nie je cele cislo: {text!r}")
    return str(number.to_integral_value())


def normalize_numeric(value: object, declared_type: str) -> int | Decimal | object:
    declared = declared_type.strip().lower()
    integer = declared == "integer"
    decimal_match = DECIMAL_TYPE_PATTERN.fullmatch(declared)
    if not integer and decimal_match is None:
        return value
    if value is None or value == "":
        return value
    if isinstance(value, bool):
        raise NumericNormalizationError("Boolean nie je numericka hodnota.")

    if isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, Decimal):
        number = value
    elif isinstance(value, float):
        number = Decimal(str(value))
    elif isinstance(value, str):
        text = unicodedata.normalize("NFKC", value).strip()
        text = CURRENCY_SUFFIX_PATTERN.sub("", text).strip()
        if not text or UNSUPPORTED_CHARACTER_PATTERN.search(text):
            raise NumericNormalizationError(f"Nepodporovany numericky text: {value!r}")
        sign = ""
        if text[:1] in {"+", "-"}:
            sign, text = text[0], text[1:]
        if not text or "+" in text or "-" in text:
            raise NumericNormalizationError(f"Neplatne znamienko: {value!r}")
        text = _strip_grouping(text)
        canonical = _integer_text(text) if integer else _decimal_text(text)
        try:
            number = Decimal(sign + canonical)
        except InvalidOperation as exc:
            raise NumericNormalizationError(f"Neplatne cislo: {value!r}") from exc
    else:
        raise NumericNormalizationError(
            f"Nepodporovany numericky typ: {type(value).__name__}"
        )

    if not number.is_finite():
        raise NumericNormalizationError("Numericka hodnota musi byt konecna.")

    if integer:
        if number != number.to_integral_value():
            raise NumericNormalizationError(f"Hodnota nie je cele cislo: {value!r}")
        return int(number)

    precision = int(decimal_match.group("precision"))
    scale = int(decimal_match.group("scale"))
    sign, digits, exponent = number.as_tuple()
    fractional_digits = max(-exponent, 0)
    integer_digits = max(len(digits) - fractional_digits, 0)
    if fractional_digits > scale or integer_digits > precision - scale:
        raise NumericNormalizationError(
            f"Hodnota {value!r} presahuje decimal({precision},{scale})."
        )
    return number


def normalize_workbook(workbook) -> dict[str, int]:
    if "data_dictionary" not in workbook.sheetnames:
        return {"normalized_values": 0}

    contract: dict[str, dict[str, str]] = defaultdict(dict)
    dictionary = workbook["data_dictionary"]
    for row in dictionary.iter_rows(min_row=2, values_only=True):
        if not row or len(row) < 3:
            continue
        sheet_name, column_name, declared_type = row[:3]
        if sheet_name is None or column_name is None or declared_type is None:
            continue
        contract[str(sheet_name)][str(column_name)] = str(declared_type).strip().lower()

    normalized_values = 0
    for sheet_name, columns in contract.items():
        if sheet_name not in workbook.sheetnames:
            continue
        worksheet = workbook[sheet_name]
        header_row = next(
            worksheet.iter_rows(min_row=1, max_row=1, values_only=True)
        )
        column_indexes = {
            str(value): index + 1
            for index, value in enumerate(header_row)
            if value is not None
        }
        for column_name, declared_type in columns.items():
            if declared_type != "integer" and DECIMAL_TYPE_PATTERN.fullmatch(declared_type) is None:
                continue
            column_index = column_indexes.get(column_name)
            if column_index is None:
                continue
            for row_number in range(2, int(worksheet.max_row or 1) + 1):
                cell = worksheet.cell(row=row_number, column=column_index)
                original = cell.value
                if original is None or original == "":
                    continue
                normalized = normalize_numeric(original, declared_type)
                if normalized != original or type(normalized) is not type(original):
                    cell.value = normalized
                    normalized_values += 1

    return {"normalized_values": normalized_values}
