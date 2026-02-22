# Full Inventory

## Canonical artifact

- File: `inventory-canonical.tsv`
- Schema: `path<TAB>size_bytes<TAB>extension<TAB>mime_type<TAB>class`
- Class values: `text` or `binary`
- Data source: full `find /home/codingai -type f` + MIME detection via `file --mime-type`

## Snapshot totals

- Total files: `33,283`
- Total bytes: `2,825,739,989`
- Text files: `29,013` (`613,880,250` bytes)
- Binary files: `4,270` (`2,211,859,739` bytes)

## Top-level file distribution

- `.vscode-server`: `14,029` files
- `workspaces`: `12,048` files
- `ai-agent`: `4,949` files
- `.npm`: `1,323` files
- `.cache`: `874` files
- `.codex`: `24` files
- `docs`: `9` files

## Dominant MIME types

- `application/javascript`: `10,923`
- `text/x-script.python`: `10,328`
- `text/plain`: `4,589`
- `application/json`: `2,485`
- `application/x-bytecode.python`: `2,073`

## Coverage statement

- Readable text inventory was generated across the entire `/home/codingai` tree, including generated/vendor/cache text artifacts.
- Binary content was inventoried by metadata only (path/size/mime/class), not decoded.

## Validation checks

1. Inventory integrity:
   - `inventory-canonical.tsv` line count equals discovered files at snapshot time.
2. Coverage integrity:
   - Every top-level entry under `/home/codingai` appears in inventory-based summaries.
3. Reproducibility:
   - Re-running inventory regenerates deterministic schema output with updated counts.
