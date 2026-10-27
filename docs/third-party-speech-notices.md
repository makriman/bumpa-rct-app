# Third-party speech components and voice attribution

Verified 27 October 2026 against local Hermes candidate image `sha256:48a64d9eb290d95c50fff2a45c0f35cded8d1b63a5b7273e4a8efc4cde468628`. This is historical candidate evidence: production-baseline reconciliation invalidated its release freeze, and the final assembled image must be checked again. This records installed license facts and upstream attribution, without concluding that every distribution condition has been satisfied. The runtime image and speech engines were not changed for this check.

## Installed engines and runtime libraries

| Component | Installed version / license metadata | Bundled notice verified |
| --- | --- | --- |
| Piper TTS | `piper-tts 1.8.0`, **GPL-3.0-or-later** | GPLv3 `COPYING`; g2pW Apache-2.0 notice; Hebrew component MIT notice |
| faster-whisper | `1.2.1`, MIT | SYSTRAN MIT `LICENSE` |
| PyAV | `av 19.0.2`, BSD-3-Clause | `LICENSE.txt`; contributor notices also present |
| CTranslate2 | `4.8.3`, MIT | Installed distribution metadata says MIT; no license file surfaced in its distribution file list. The exact release's upstream MIT notice is included in this documentation. |
| ONNX Runtime | `1.31.0`, MIT | Microsoft MIT `LICENSE` and full `ThirdPartyNotices.txt` |

Installed paths, hashes and image identity are in [installed-speech-licenses.json](../tests/hermes-canary/artifacts/installed-speech-licenses.json). Piper's installed GPL text hash is `0ae0485a5bd37a63e63603596417e4eb0e653334fa6c7f932ca3a0e85d4af227`; it matches `COPYING` at the [Piper 1.8.0 publishing commit](https://github.com/OHF-Voice/piper1-gpl/blob/639388b6317fc4731e91d53da42aea68fd4166ff/COPYING). That commit's [setup.py](https://github.com/OHF-Voice/piper1-gpl/blob/639388b6317fc4731e91d53da42aea68fd4166ff/setup.py) and [PyPI 1.8.0 metadata](https://pypi.org/project/piper-tts/1.8.0/) explicitly identify GPL-3.0-or-later. The earlier MIT-licensed Piper project must not be used to describe this installed version.

Preserve the supplied copyright/license notices and disclaimers. The sidecar [third-party-speech](third-party-speech/) directory contains the checked notice copies and model cards, with their [manifest](third-party-speech/MANIFEST.json). Preserve the full ONNX Runtime third-party notice rather than reducing its contents to the package's top-level MIT metadata. A read-only search of the inspected image found no model cards, README or license files under `/opt/hermes/models`, and no CTranslate2 license/notice file under its installed package, distribution metadata or `.libs` directories; repository sidecar notices fill the documentation gap without changing that image.

The Bumpa compatibility patch changes the installed faster-whisper decoder only by removing the unsupported PyAV `metadata_errors` argument. The reviewed patch and its source hashes are in [`patch_speech_compat.py`](../infra/hermes/patch_speech_compat.py); it does not change these licenses.

## Installed voice: en_GB Alba medium

The installed file is `en_GB-alba-medium.onnx`, SHA-256 `401369c4a81d09fdd86c32c5c864440811dbdcc66466cde2d64f7133a66ad03b`. Its configuration hash is `aa965a2f02ecced632c2694e1fc72bbff6d65f265fab567ca945918c73dd89f4`. It is the pinned `rhasspy/piper-voices` artifact at revision `0d907f158acc877ddeebcbf827659ee13bea8bcd`, English Great Britain, one speaker, medium quality, 22,050 Hz. The installed JSON identifies dataset `alba`; it contains no license field.

The exact [Alba model card](https://huggingface.co/rhasspy/piper-voices/blob/0d907f158acc877ddeebcbf827659ee13bea8bcd/en/en_GB/alba/medium/MODEL_CARD) identifies its dataset under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) and says the model was fine-tuned from US English Lessac medium. The card's SHA-256 is `fa166b1779404c470b0b6b4ba0238bc4a35bf89d2cd130c6788f697188b737d6`.

Dataset attribution: **Cassia Valentini-Botinhao and Junichi Yamagishi (2019), Alba speech corpus, University of Edinburgh, DOI [10.7488/ds/2506](https://doi.org/10.7488/ds/2506)**. Source and creator attribution are recorded by the [University of Edinburgh dataset page](https://datashare.ed.ac.uk/handle/10283/3270). The dataset is CC BY 4.0; its supplied license note also preserves voice-talent moral/personality rights and prohibits derogatory use. Retain appropriate credit, the license link and an indication of upstream adaptation; do not imply endorsement by the creators or speaker. Bumpa has not retrained or edited these pinned voice weights; the upstream adaptation is the fine-tuning stated by its card.

The card labels the **dataset** license. It does not separately state a voice-weight license grant. The installed config supplies no additional grant. This distinction matters for publication of a container that includes the weights.

## Base-training lineage requiring release clarification

The pinned [Lessac medium model card](https://huggingface.co/rhasspy/piper-voices/blob/0d907f158acc877ddeebcbf827659ee13bea8bcd/en/en_US/lessac/medium/MODEL_CARD), SHA-256 `ce49eb457742208166d399a40cdec2c7fa9db77960930031564ab56f12882645`, points to the Edinburgh Blizzard 2013 Lessac dataset and its own license. The primary [dataset page](https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/) describes non-commercial use, and the linked [research license](https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html) restricts the specified materials to research and excludes commercial speech products/services.

These are verified dataset terms and a verified model-training lineage. They do **not** establish a legal conclusion about whether those terms apply to the derived Alba weights. No separate permission, weight-specific clarification or applicable commercial grant was found in the inspected cards/config. Before public redistribution or commercial activation of this exact voice, the release owner must record the applicable rights/permission basis or obtain clarification. This notice does not accept a license or contact a rights holder, and no replacement voice was selected.

## Recognition model

The pinned `Systran/faster-whisper-base` revision `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66` is an OpenAI Whisper base conversion for CTranslate2. Its exact [README/model card](https://huggingface.co/Systran/faster-whisper-base/blob/ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66/README.md), SHA-256 `b922acef3616e27b1d416260b63ba7114c732f0315012006781af2da69cd1ebf`, declares `license: mit`. OpenAI's [Whisper README](https://github.com/openai/whisper#license) states that code and model weights are MIT licensed. Preserve its attribution and the applicable MIT notice with the model distribution. Package license metadata and the model license are separate records.

## Packaging gates

1. Preserve this notice, exact model cards and full bundled notices as release materials. The inspected image does not gain these documentation files merely because they were added to the repository; confirm how image recipients receive the sidecar and repeat the inventory for the final assembled image.
2. For Piper object-code redistribution, record the selected GPLv3 distribution method and matching Corresponding Source/build material. GPLv3 sections 4–6 specify notice/source conditions. An embedded `COPYING` or repository homepage link alone is not evidence that the release has provided matching source. The [PyPI source distribution](https://pypi.org/project/piper-tts/1.8.0/#files) and publishing commit are provenance references; the complete matching-source receipt remains a release-owner task.
3. Resolve and record the exact Alba/Lessac-derived voice's applicable distribution/commercial rights basis described above before public image publication or commercial activation. A dataset's CC BY tag and the engine's GPL label do not answer each other's questions.
4. Preserve MIT/BSD/Apache notices and full transitive third-party notices. This is a focused speech inventory, not a complete container SBOM or whole-application licensing certification.

Runtime verification is separate: local Piper synthesis, Whisper WAV/OGG transcription and decoder-health tests passed. They provide no evidence about legal permission, broader speech quality or actual call capacity. No public image, account change, real call or production action was performed by this license check.
