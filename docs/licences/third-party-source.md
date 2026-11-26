# Third-party notices and corresponding source

This document accompanies VencOverlay's installation and third-party source
archives. The included licences apply to their respective components; they do
not replace the project's GPL licence or impose additional restrictions on
recipients' rights. The source and notices are available without an additional
charge from the same GitHub release as the executable:

https://github.com/caminashell/vencoverlay/releases

## Where to find source and licences

- `source/vencoverlay-source.zip` in the installation archive contains the
  project source, build and installation scripts, dependency lockfile and
  developer documentation for that build.
- `dist/vencoverlay-vencord.zip` in the installation archive contains
  `source/vencord-source.zip`, the pinned Vencord source, VencOverlay plugin,
  frontend build wrapper, lockfile and rebuild instructions. Its `runtime/`
  directory includes GPL licences, original attribution and generated legal
  notices. The `debug/` directory contains source maps.
- `licenses/third-party-evidence.zip` in the installation archive contains
  the build-specific native and frontend inventories and collected original
  licences and notices. Consult `native/inventory.json` and
  `frontend/inventory.json` for exact versions, package ownership and notice
  locations. Installed system libraries are not necessarily redistributed.
- `licenses/source-notices.zip` contains supplementary notices, including
  Pycairo's LGPL 2.1 and MPL 1.1 texts, additional Python and npm notices and
  the vendored component notices described below.
- `licenses/source-asset.json` identifies the exact companion archive and its
  SHA-256. Download `vencoverlay-<version>-third-party-source.tar.gz` alongside
  the installation archive. Its `source-manifest.json` identifies every
  dependency source archive, download origin, checksum and build identity.
  `rpm/` contains matching Fedora source RPMs including distribution patches
  and spec files; `python/` contains locked Python source distributions;
  `npm/` contains the exact locked frontend packages, including the pinned
  gifenc Git archive. `notices/` contains the supplemental licence material.

The source of VencOverlay's original overlay is attributed in the project
README and the original plugin author is credited in the frontend attribution.

## Frontend notices reviewed for this source pin

Vencord revision: `d086992fb674299a2fa05ac6432edc3a777389f3`.

| Component | Attribution and source | Licence material |
|---|---|---|
| @vap/core 0.0.12 | Copyright (c) 2022 Justice Almanzar. Published npm `gitHead`: `a6d3c6af4e5a533f91363d2c617b849b9f08ce10`. [Exact upstream licence](https://github.com/Vap0r1ze/vapcord/blob/a6d3c6af4e5a533f91363d2c617b849b9f08ce10/LICENSE); [source tree](https://github.com/Vap0r1ze/vapcord/tree/a6d3c6af4e5a533f91363d2c617b849b9f08ce10). | `notices/npm/@vap+core-0.0.12.tgz/LICENSE` |
| BetterDiscord addon metadata parser | Copyright 2023 BetterDiscord contributors; Copyright 2023 Vendicated and Vencord contributors. `src/main/themes/index.ts` in the nested Vencord source. | Apache 2.0: `notices/vendored/Apache-2.0.txt` and original source/legal headers |
| idb-keyval v6.2.0 | Copyright 2016 Jake Archibald; Copyright 2022 Vendicated. `src/api/DataStore/index.ts` in the nested Vencord source. | Apache 2.0: `notices/vendored/Apache-2.0.txt` and original source/legal headers |
| crxToZip | Copyright (c) 2013 Rob Wu. `src/main/utils/crxToZip.ts` in the nested Vencord source. | `notices/vendored/crxToZip-MPL-2.0.txt` |
| discord-intl | Copyright 2024 Discord, Inc., as stated in the redistributed `src/utils/intlHash.ts`. [Upstream project](https://github.com/discord/discord-intl). | `notices/vendored/discord-intl-MIT.txt` |
| apng-canvas v2.1.2 | Copyright 2011–2019 David Mzareulyan, as stated in the redistributed `src/utils/apng.ts` (heavily modified and converted to TypeScript). [Original v2.1.2 licence](https://github.com/davidmz/apng-canvas/blob/v2.1.2/LICENSE) states copyright (c) 2011 David Mzareulyan. Both attributions are preserved. | `notices/vendored/apng-canvas-MIT.txt` |

The `crxToZip` source file remains available under MPL 2.0. Obtain it by
extracting `source/vencord-source.zip` from the frontend archive at the
location above; its source header and the complete MPL 2.0 text are retained.
The original component licence terms and generated attribution headers remain
applicable. The four other mapped npm packages retain their original licences
in the inventories and companion notices.

## Native rebuilding and modification

PyGObject and Pycairo are bundled Python extensions, not merely system
requirements. See the project source archive's `docs/development.md`, section
"Rebuilding with modified dependencies", for a procedure to replace the locked
Python sources in an independent development tree, rebuild, and run a modified
executable. On 26 November 2026, the `0.1.0.dev1` candidate was successfully
rebuilt on Fedora 43 x86-64 with modified PyGObject 3.58.1 and Pycairo 1.29.2 C
extensions using CPython 3.14.10 and Nuitka 4.3.5. Both diagnostic markers
appeared in the rebuilt executable's successful native runtime check. The
Pycairo local-source packaging correction is documented in that procedure.
The exact source RPMs permit corresponding Fedora libraries and introspection
data to be rebuilt with modifications on a compatible system; individual RPM
rebuilds have not been exercised by this test.
The isolated Nuitka GI discovery change is expressed in `build.sh` and applied
at build time to the locked Nuitka source; the original licence and runtime
exception accompany the distribution. There is no requirement for permission
from VencOverlay to rebuild, modify or debug library modifications.

## Review scope

This document supplements the original notices; it is not legal certification.
The release maintainer must verify the matching source archive, preferred
source for modification, any additional upstream NOTICE files, compiler/runtime
embedded code, and the applicability of the verified Python extension rebuild
procedure to the final artifacts before publishing. The current tooling records
the candidate as pending human review; the successful modification test does
not constitute comprehensive licence clearance.
