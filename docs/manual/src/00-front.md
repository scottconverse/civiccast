# About this manual {#about}

CivicCast is a Windows program for a public-access (PEG) station or a small city to schedule, put meetings on the air, record them, and publish them for residents. This manual guides station staff through those tasks and the IT work that supports them.

## Who Reads What

| If you are... | Read |
| --- | --- |
| A volunteer, camera operator, video editor, records clerk or PEG station employee | **Part I** (chapters 1 to 8): what to click, what you will see, what to do when something looks wrong |
| The IT person at a small city who installs and runs CivicCast | **Part II** (chapters 9 to 15): planning, installing, configuring, running, securing, troubleshooting |
| An IT reviewer, integrator or the curious | **Part III** (chapter 16): how the system is built, with diagrams |
| Anyone looking something up | **Part IV**, the appendices: commands, web interface, settings, files and ports, status words, roles, checklists, evidence, glossary, licenses, release history |

## Where the older guide names went

Earlier releases split this manual into separate guides. Their names are still used in other documents, so here is where each one now lives in the CivicCast User Manual.

- **Admin Quick Guide** (also the Admin Guide): Part II, chapters 9 to 15, and Appendices A to F.
- **Meeting Operator Quick Guide** (also the Meeting Operator Guide): Part I, chapters 3 and 4.
- **Records Clerk Quick Guide** (also the Records Clerk Guide): Part I, chapters 5 and 6.
- **Technical Operations Reference**: Part II, chapters 11 to 15, Part III (chapter 16) and Appendices A to E.

## How to read the notes in this manual

> **Note:** background you may need.

> **Tip:** a faster or safer way to do something.

> **Warning:** something that can change what is on the air, delete data, or cannot be undone.

> **Known issue (version):** behavior checked in the named version differs from what the screen suggests; the note gives its scope and a safe next step. An explicit current-version statement describes a current limitation; an older label alone is historical.

> **For IT staff:** a pointer from the non-technical part to the technical chapter that has the detail.

## Words used throughout

- **Station** means the Windows computer running CivicCast and everything it controls.
- **Staff console** (also called the *operator console*) is the web page staff use to run the station. It opens at the station's address followed by `/operator/`.
- **Resident portal** is the public web page where residents watch live programs, browse recordings and read the schedule.
- **Channel** is one program stream, like a cable channel. A station can run several.
- **Asset** is one recording or media file stored in CivicCast.
- Schedule, Program Guide, recording and auto-schedule times use the station's configured time zone. Some reports and exports still use **UTC**; each chapter names the relevant time basis.

## How this manual was made

The manual was written from the product's own code and from a screen-by-screen inventory of the staff console, the resident portal and the installer, then checked against that code. Screens were photographed on a lab station that holds sample (mock) data only. Where the code could not settle a question, the manual says nothing rather than guessing. The version of the software this manual describes is shown on the cover; if you run a different version, details may differ.

## Edition and package evidence

This manual describes the 1.0.0-rc.6 edition. The manual bundled with a release describes that release; source documentation can describe newer controls. For publication, download, installation, and acceptance status, use the matching release page and the version-specific verification record; do not infer status from this manual.

The October 17 documentation correction reconciles operating instructions and
refreshes the CLI, API and settings references. Existing installed Help may still
contain the older wording until its application package is updated.

This RC6 edition puts AC-3 sound and a constant bit rate on a cable output while the web output stays AAC. Like RC5, it keeps live captions ahead of programme preparation on a busy station and refuses XML documents at media intake. Like RC4, it retains RC1 agenda setup, offline English OCR and managed agenda recovery, and updates first-install permissions, caption diagnostics, second-level durations, resident guide visibility and operator error guidance. It also covers: remote guests in channel output, bulletin-board live video and audio, native viewer improvements, and the publication, import and underwriting work shipped in Beta 17 and 18. The manual also covers staff accounts, station backup and restore, network access, the restricted service account, station-local scheduling, worker recovery, broadcast outputs and recorded captions. The [Beta 18 package record](https://github.com/scottconverse/civiccast/blob/main/docs/releases/v1.0.0-beta.18-verification.md) remains historical. For publication, download, installation, and acceptance status, use the matching release page and the version-specific verification record for the package you use.

Check the [releases page](https://github.com/scottconverse/civiccast/releases) for available downloads. For an installed package, use its matching Help and verification record. A **historical observation** describes behavior or evidence from the named earlier release. A **Known issue (version)** names the version in which the behavior was checked; the label alone does not say whether it remains true now. Historical host measurements remain distinct from installation checks of any package.

## Where to get help and report problems

Report problems and read release notes at the project page: <https://github.com/scottconverse/civiccast>. Check <https://github.com/scottconverse/civiccast/releases> for current downloads and publication status, then read the verification record for the exact package you use. Dated records for earlier packages remain available as release history.

<!-- SOURCES: docs/releases/v1.0.0-beta.12-verification.md; docs/releases/v1.0.0-beta.11-verification.md (historical host evidence); docs/releases/v1.0.0-beta.10-verification.md (historical appendix only); docs/releases/release-truth.yaml; ops/docs-sprint/MANUAL-STYLE.md -->
