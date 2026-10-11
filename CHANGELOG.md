# Changelog

## Forge Neo 2.30 compatibility - 2026-10-11

- Follow the relocated attention parser in Forge Neo 2.30
- Keep the legacy parser import as a fallback for older versions
- UI display confirmed on Forge Neo 2.30; TIPO inference remains unverified

## 1.0.0 - 2026-09-01

Initial public release.

### Added

- Independent Z-Tipo append workflow
- Preserve existing Positive Prompt
- Append TIPO result to existing Positive Prompt
- Duplicate tag removal
- Underscore / space normalized duplicate detection
- Weighted tag duplicate detection
- Safe Ban Tags regex handling
- Use the model currently selected in Z-Tipo
- Persistent settings
- Persistent UI open state
- txt2img support
- img2img support

### Compatibility

- Designed for sd-webui-forge-neo
- Requires z-tipo-extension
