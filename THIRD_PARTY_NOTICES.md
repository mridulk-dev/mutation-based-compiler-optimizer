# Third-party material

This file records the provenance and licensing status of the third-party material in this
repository. It does **not** choose a licence for the project's own code. As of this
release, the repository has no root `LICENSE` file. See "Licensing recommendation" in
`docs/report.md`.

## `jotai_unique_300/` (source collection)

- **What it is:** 300 byte-distinct C files selected from the Jotai benchmarks
  (anghaLeaves). File SHA-256 hashes are listed in `jotai_unique_300/manifest.csv`, and the
  dataset builder verifies them.
- **Collection:** <https://github.com/lac-dcc/jotai-benchmarks>. The repository declares
  **GPL-3.0** at repository level (checked on the GitHub repository page, 2026-10-12).
- **Origin of the functions:** AnghaBench, which mined them from open-source GitHub
  projects. Neither Jotai nor AnghaBench states the originating project or licence of each
  function. The flattened path in each file name is only a hint.
- **Status:** the upstream licence of each individual function is **unverified**.
- **Citation:** C. C. Kind, M. Canesche, F. M. Q. Pereira. *Jotai: a Methodology for the
  Generation of Executable C Benchmarks.* Tech. Report 02-2022, UFMG, 2022.

## Dataset group C (`dataset/miniarith-v1`, records `EXT-JOTAI-*`)

- **Records:** 300 records, one per Jotai file. 7 are accepted MiniArith translations; the
  other 293 are kept as incompatible or duplicate records. The manifest stores the verbatim
  C function text and the file hash for each.
- **Where the 7 accepted translations come from:** all of them derive from functions in one
  upstream file. The path hint is
  `BGAQRCode-Android/zbar/src/main/jni/libiconv-1.15/tools/cjk_tab_to_h.c`, which suggests a
  copy of GNU libiconv 1.15 vendored in that project. That file's own licence header was
  **not** checked, so these records stay `license.status = "unverified"`.
- **Redistribution:** treat these records as derived from GPL-3.0-collection material with
  unverified upstream terms. Anyone who needs clean licensing should exclude
  `group == "external"`. The project's own licence, once chosen, must not be presented as
  covering these records or the files in `jotai_unique_300/`.

## Software dependencies

- Python standard library.
- matplotlib (plots only), Matplotlib licence (PSF-based).
- gcc (optional; used only for cross-checking group C).
