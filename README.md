# LibreHaptics-Catalog

Central, auto-synced **Schema v3** catalog of LibreHaptics game mods and `.lhfxpack` effect bundles, split into **Standalone VR** and **PCVR / PC** feeds.

## Catalog Endpoints (1 HTTP GET, no GitHub API rate limit)

- **Standalone VR (Meta Quest — default in Player Lite):**
  `https://raw.githubusercontent.com/LibreHaptics/LibreHaptics-Catalog/main/standalone-games.json`
  *(Alias: `quest-games.json`)*
- **PCVR (Windows / Linux OpenXR & SteamVR):**
  `https://raw.githubusercontent.com/LibreHaptics/LibreHaptics-Catalog/main/pcvr-games.json`
- **All PC (PCVR + PC Flat):**
  `https://raw.githubusercontent.com/LibreHaptics/LibreHaptics-Catalog/main/pc-games.json`
- **Combined (all platforms):**
  `https://raw.githubusercontent.com/LibreHaptics/LibreHaptics-Catalog/main/catalog.json`

## How It Works

1. Add your mod repository to [`sources.json`](sources.json) (it must contain a Schema v3 `librehaptics-mod.json` and publish binaries + `.lhfxpack` + `.sha256` via **GitHub Releases**).
2. The `Sync Catalog` GitHub Action runs every 30 minutes (and on push to `sources.json` or manual `Run workflow`), resolving `assetPattern` entries against each repo's latest Release and updating the generated `*-games.json` files automatically.

## License

Dual-licensed under **MIT** or **Apache-2.0**.
