# Engine attribution and adaptation

Selected helpers originate in Microsoft's MIT-licensed
[ai-audio-descriptions](https://github.com/microsoft/ai-audio-descriptions)
at tree `be76687c6cc4ee9667328bcc380d76c1bb95ab94`.
The original grant is preserved in `LICENSE.md` and source headers.

The active CivicCast path uses segment/gap, FFmpeg and WebVTT helpers.
Local Gemma and Pocket TTS adapters live above this directory; upstream Azure
authoring/synthesis entrypoints are not invoked. Adaptations include package-relative
imports, strict zip, native typing/style fixes and a file/pipe protocol allowlist
for FFmpeg inputs. No upstream web UI or hosted service is started.
