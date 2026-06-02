from __future__ import annotations

from pathlib import Path


def _yaml_double_quoted(value: str) -> str:
    """Render value as a safe YAML double-quoted scalar."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def upsert_top_level_section_fields(
    settings_path: Path,
    section: str,
    fields: dict[str, str],
) -> bool:
    """Update or append simple string fields under a top-level YAML section."""
    lines = settings_path.read_text(encoding="utf-8").splitlines()
    header = f"{section}:"
    section_start: int | None = None
    section_end = len(lines)

    for idx, line in enumerate(lines):
        if line.strip() == header and not line.startswith(" "):
            section_start = idx
            break

    if section_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(header)
        for key, value in fields.items():
            lines.append(f"  {key}: {_yaml_double_quoted(value)}")
        settings_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True

    for idx in range(section_start + 1, len(lines)):
        if lines[idx].strip() and not lines[idx].startswith(" "):
            section_end = idx
            break

    found = {key: False for key in fields}
    changed = False
    for idx in range(section_start + 1, section_end):
        stripped = lines[idx].strip()
        for key, value in fields.items():
            if stripped.startswith(f"{key}:"):
                found[key] = True
                new_line = f"  {key}: {_yaml_double_quoted(value)}"
                if lines[idx] != new_line:
                    lines[idx] = new_line
                    changed = True

    insert_lines = [
        f"  {key}: {_yaml_double_quoted(fields[key])}"
        for key, was_found in found.items()
        if not was_found
    ]
    if insert_lines:
        lines[section_end:section_end] = insert_lines
        changed = True

    if changed:
        settings_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed

