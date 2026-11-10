# Preserved firmware history: taxom.1 through taxom.7

Each version has its own `versions/1.7.0-taxom.N` branch. The source tree in each published snapshot matches the corresponding original version exactly.

The connected GitHub publisher creates new commits and cannot retain the original author/committer timestamps in the Git object. Consequently, published commit IDs for versions 4–7 differ. The original IDs and dates are recorded in those commit messages.

`original-taxom.1-7.bundle` preserves the exact original Git objects, authors, dates and the seven `v1.7.0-taxom.N` tags. It includes the original development branch through version 7 and requires upstream base commit `e9a060958c9dcf82f24353e36c2282b057690be4`, which is already present in this repository. The bundle contains no Git configuration or credentials.

| Version | Published snapshot | Original commit |
| --- | --- | --- |
| 1.7.0-taxom.1 | [`c8858b9`](https://github.com/Taxom/riden-dongle/tree/versions/1.7.0-taxom.1) | `c8858b93423b48bef6eb9b6a6a37b4b76da11d53` |
| 1.7.0-taxom.2 | [`6a46ac3`](https://github.com/Taxom/riden-dongle/tree/versions/1.7.0-taxom.2) | `6a46ac3f249e15887d0fe41c8427feba9c9f0fde` |
| 1.7.0-taxom.3 | [`033810c`](https://github.com/Taxom/riden-dongle/tree/versions/1.7.0-taxom.3) | `033810c680108573fa47f62bf4cd6724e812f177` |
| 1.7.0-taxom.4 | [`ab23d2b`](https://github.com/Taxom/riden-dongle/tree/versions/1.7.0-taxom.4) | `e009c6e1aec4cb34bd89b657b8ae5caae6b74546` |
| 1.7.0-taxom.5 | [`8041d4b`](https://github.com/Taxom/riden-dongle/tree/versions/1.7.0-taxom.5) | `0f87d2d5ee0ce31371d50f8ca26a9fdc96b45d34` |
| 1.7.0-taxom.6 | [`69e05dd`](https://github.com/Taxom/riden-dongle/tree/versions/1.7.0-taxom.6) | `1ff0ce0702d68259136789cdaef4d121520e3e03` |
| 1.7.0-taxom.7 | [`1e13471`](https://github.com/Taxom/riden-dongle/tree/versions/1.7.0-taxom.7) | `0a48cc9a05aab31f47f4fd270cd1842234ece25c` |

To import the original tags into a clone of this repository:

```sh
git bundle verify history/original-taxom.1-7.bundle
git fetch history/original-taxom.1-7.bundle 'refs/tags/v1.7.0-taxom.*:refs/tags/v1.7.0-taxom.*'
git log --oneline v1.7.0-taxom.1..v1.7.0-taxom.7
```

To inspect a particular original snapshot, use `git show v1.7.0-taxom.4:FORK_NOTES.md` or check out its version tag. GitHub's version branches provide the same source snapshots directly in the web interface.

The standalone Windows/Python concurrent Modbus + HTTP control tester is documented in [`tools/multi_client_test/README_RU.txt`](../tools/multi_client_test/README_RU.txt).

Later stages continue as separate commits and version branches:

- [`versions/1.7.0-taxom.8`](https://github.com/Taxom/riden-dongle/tree/versions/1.7.0-taxom.8): persistent optional detailed diagnostics, default off; core V2 configuration stays compatible with earlier firmware. The original 1–7 snapshots and bundle above remain unchanged.
- [`versions/1.7.0-taxom.9`](https://github.com/Taxom/riden-dongle/tree/versions/1.7.0-taxom.9): strict HTTP/live-register input validation and the standalone final OFF/ON diagnostics acceptance tester, including expected rejections of malformed or out-of-range requests.
- [`versions/1.7.0-taxom.10`](https://github.com/Taxom/riden-dongle/tree/versions/1.7.0-taxom.10), source commit `f31e3a39a3d0cab2adc6d7acf08f5db1b06ed227`: per-quantity web/API precision, RD6006P 3/4 decimal places, active-range formatting and decimal setpoint round-trip preservation. The build/CI and 94 host groups passed; the user installed this build and confirmed the displayed decimal places. The broader hardware acceptance remains the version 9 run recorded in `FORK_NOTES.md`.

