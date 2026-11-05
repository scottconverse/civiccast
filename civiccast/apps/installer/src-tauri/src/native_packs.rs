// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors

use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine as _;
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use zip::{CompressionMethod, ZipArchive};

const PACK_SCHEMA_VERSION: u32 = 1;
const PACK_PRODUCT: &str = "civiccast-native";
const MANIFEST_NAME: &str = "manifest.json";
const SIGNATURE_NAME: &str = "manifest.sig";
const PAYLOAD_PREFIX: &str = "payload/";
const MAX_MANIFEST_BYTES: u64 = 4 * 1024 * 1024;
const CAPTION_COMPONENT: &str = "captions-large-v3";
/// <installer-path-audit BL-08> The MANDATORY caption baseline's component id.
///
/// Its absence from this file was the finding: a grep for `captions-floor` in
/// `native_packs.rs` found none, so the owner-designated mandatory caption
/// model reached no branch of `validate_component_contract` at all, and the
/// only machine checks on it were the four values the publisher chose and
/// signed in one operation.
const CAPTION_FLOOR_COMPONENT: &str = "captions-floor";
const WHISTLE_COMPONENT: &str = "captions-whistle";
const WHISTLE_MODEL_SHA256: &str =
    "b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb";
const WHISTLE_ENGINE_WHEEL_SHA256: &str =
    "4f5fc86abfc50d551cdb237a34b501f36d82d4b6f5911ee7bec4e9532d44dd95";
const WHISTLE_ENGINE_DLL_SHA256: &str =
    "de2e2c39cd311fbd9971fad4736abc329ed970653674c203e149c4ef27fd1c62";
const SOURCE_BOUND_COMPONENTS: [&str; 2] = [
    "native-app-payload",
    "native-server-binaries",
];
// `pub(crate)`: also the single source of truth `component_acquisition.rs`
// resolves the large tier's HuggingFace download URL and pinned per-file
// hashes from (same posture as the floor-tier constants below) -- enrolled
// in the production acquisition catalog 2026-08-15 by owner ruling
// ("the user should get the better caption model if the hardware supports
// it"). Never re-transcribe these into another module.
pub(crate) const CAPTION_MODEL_ROOT: &str = "models/faster-whisper-large-v3";
pub(crate) const CAPTION_LARGE_TIER_MODEL_REPOSITORY: &str = "Systran/faster-whisper-large-v3";
pub(crate) const CAPTION_LARGE_TIER_MODEL_REVISION: &str =
    "edaa852ec7e145841d8ffdb056a99866b5f0a478";
pub(crate) const CAPTION_MODEL_FILES: [(&str, u64, &str); 6] = [
    (
        "README.md",
        2_052,
        "39e96252229f5a3d0141dc81afb65a36fd205461ac21e5b70f2cd1248ef0082c",
    ),
    (
        "config.json",
        2_394,
        "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9",
    ),
    (
        "model.bin",
        3_087_284_237,
        "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1",
    ),
    (
        "preprocessor_config.json",
        340,
        "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
    ),
    (
        "tokenizer.json",
        2_480_617,
        "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca",
    ),
    (
        "vocabulary.json",
        1_068_114,
        "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
    ),
];
const CAPTION_SELF_TEST_PATH: &str = "self-test/jfk.wav";
// `pub(crate)`: also read directly by `main.rs`'s post-acquisition large-v3
// addendum self-test (`verify_self_test_audio_fixture`), which re-verifies
// the mandatory floor tier's already-staged jfk.wav fixture against this
// SAME pinned identity before reusing it read-only -- never a second,
// hand-copied literal.
pub(crate) const CAPTION_SELF_TEST_BYTES: u64 = 352_078;
pub(crate) const CAPTION_SELF_TEST_SHA256: &str =
    "59dfb9a4acb36fe2a2affc14bacbee2920ff435cb13cc314a08c13f66ba7860e";
// Mirrors `civiccast.native.caption_tiers`: each caption model tier owns its
// OWN complete, pinned file inventory. large-v3 re-uses the existing pinned
// identity above verbatim; the floor tier used to be a placeholder,
// structurally identical to a bound tier, pending the owner's R7-measurement
// binding -- that measurement closed 2026-07-30
// (OWNER-DECISION-caption-adaptive-tier.md's BINDING section named `medium`),
// and the constants below are that binding, transcribed verbatim from the
// real bound entry in `civiccast.native.caption_tiers.CAPTION_TIER_REGISTRY
// [FLOOR_TIER_ID]` (never re-derived here). `test_native_installer_identity
// .py::test_caption_floor_tier_binding_matches_across_python_and_rust`
// cross-checks this transcription against that Python source on every run.
// `pub(crate)`: also referenced directly by `hardware_inventory.rs`'s caption
// tier recommendation (never re-transcribed as a second string literal --
// same single-source-of-truth posture as the pub(crate) floor-tier identity
// constants a few lines below).
pub(crate) const LARGE_V3_TIER_ID: &str = "large-v3";
pub(crate) const FLOOR_TIER_ID: &str = "floor";
pub(crate) const CAPTION_FLOOR_TIER_MODEL_ROOT: &str = "models/faster-whisper-medium";
// Provenance-only identity (never consumed by verification logic below --
// exactly like large-v3's own repository/revision literals a few dozen
// lines down in `validate_component_contract`, which are checked against
// pack METADATA, not read back off any `CaptionTierSpec`). Kept here purely
// so the cross-language pin can assert Rust and Python agree on where these
// pinned bytes came from, not just what they hash to.
//
// `pub(crate)`: also the single source of truth `component_acquisition.rs`
// resolves its HuggingFace download URL and pinned per-file hashes from --
// see that module's `caption_floor_tier_source()`/`caption_floor_tier_files()`.
// Never re-transcribe these into another module; that is exactly the
// cross-tier-hash-substitution defect `verify_caption_pack_tiers` exists to
// prevent, just relocated to a second copy of the pin instead of a second
// verifier.
pub(crate) const CAPTION_FLOOR_TIER_MODEL_REPOSITORY: &str = "Systran/faster-whisper-medium";
pub(crate) const CAPTION_FLOOR_TIER_MODEL_REVISION: &str =
    "08e178d48790749d25932bbc082711ddcfdfbc4f";
pub(crate) const CAPTION_FLOOR_TIER_MODEL_FILES: [(&str, u64, &str); 4] = [
    (
        "config.json",
        2_257,
        "3622a2ddc41ec0e0fd4e68c13c6830f03b90c38d89aaad184de02c8c642cf807",
    ),
    (
        "model.bin",
        1_527_906_378,
        "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae",
    ),
    (
        "tokenizer.json",
        2_203_239,
        "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
    ),
    (
        "vocabulary.txt",
        459_861,
        "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
    ),
];
const CAPTION_NO_TIER_FILES: [(&str, u64, &str); 0] = [];
const OLLAMA_MODEL_LOCK_JSON: &str =
    include_str!("../../../../../native-windows-ollama-models.lock.json");

#[derive(Debug, Clone)]
pub struct PackTrust {
    pub key_id: String,
    pub public_key: VerifyingKey,
}

#[derive(Debug, Clone, Serialize)]
pub struct VerifiedPack {
    pub path: PathBuf,
    pub sha256: String,
    pub component: String,
    pub product_version: String,
    pub compatible_core: String,
    pub signing_key_id: String,
    pub file_count: usize,
    pub total_bytes: u64,
    pub metadata: BTreeMap<String, Value>,
    pub files: Vec<VerifiedPackFile>,
}

#[derive(Debug, Clone, Serialize)]
pub struct VerifiedPackFile {
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PackManifest {
    schema_version: u32,
    product: String,
    component: String,
    product_version: String,
    compatible_core: String,
    signing_key_id: String,
    file_count: usize,
    total_bytes: u64,
    files: Vec<PackManifestFile>,
    metadata: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct PackManifestFile {
    path: String,
    bytes: u64,
    sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct OllamaModelLock {
    schema_version: u32,
    registry: String,
    ollama_runtime_version: String,
    models: BTreeMap<String, OllamaModelLockEntry>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct OllamaModelLockEntry {
    component: String,
    config: OllamaBlobIdentity,
    layers: Vec<OllamaLayerIdentity>,
    manifest_bytes: u64,
    manifest_sha256: String,
    repository: String,
    tag: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct OllamaBlobIdentity {
    bytes: u64,
    sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct OllamaLayerIdentity {
    bytes: u64,
    media_type: String,
    sha256: String,
}

pub fn embedded_pack_trust() -> Result<PackTrust, String> {
    let key_id = option_env!("CIVICCAST_PACK_SIGNING_KEY_ID")
        .ok_or_else(|| "This bootstrap has no embedded pack signing key id.".to_string())?;
    let encoded = option_env!("CIVICCAST_PACK_PUBLIC_KEY_BASE64")
        .ok_or_else(|| "This bootstrap has no embedded pack public key.".to_string())?;
    if key_id.starts_with("development-")
        && option_env!("CIVICCAST_ALLOW_DEVELOPMENT_PACK_KEY") != Some("1")
    {
        return Err(
            "This bootstrap contains a development pack key without the explicit non-release build switch."
                .to_string(),
        );
    }
    let decoded = BASE64
        .decode(encoded)
        .map_err(|error| format!("Embedded pack public key is invalid base64: {error}"))?;
    let key_bytes: [u8; 32] = decoded
        .try_into()
        .map_err(|_| "Embedded pack public key must contain exactly 32 bytes.".to_string())?;
    let public_key = VerifyingKey::from_bytes(&key_bytes)
        .map_err(|error| format!("Embedded pack public key is invalid: {error}"))?;
    Ok(PackTrust {
        key_id: key_id.to_string(),
        public_key,
    })
}

pub fn verify_pack(
    path: &Path,
    trust: &PackTrust,
    expected_component: Option<&str>,
    expected_product_version: Option<&str>,
    expected_compatible_core: Option<&str>,
) -> Result<VerifiedPack, String> {
    open_and_verify_pack(
        path,
        trust,
        expected_component,
        expected_product_version,
        expected_compatible_core,
    )
    .map(|(verified, _archive)| verified)
}

pub(crate) fn open_and_verify_pack(
    path: &Path,
    trust: &PackTrust,
    expected_component: Option<&str>,
    expected_product_version: Option<&str>,
    expected_compatible_core: Option<&str>,
) -> Result<(VerifiedPack, ZipArchive<File>), String> {
    if !path.is_file() {
        return Err(format!(
            "Native component pack is missing: {}",
            path.display()
        ));
    }
    reject_reparse_path(path)?;
    let mut file = open_pack_file(path)?;
    let pack_sha256 = sha256_reader(&mut file, "component pack")?;
    file.seek(SeekFrom::Start(0))
        .map_err(|error| format!("Could not rewind native component pack: {error}"))?;
    let mut archive = ZipArchive::new(file)
        .map_err(|error| format!("Native component pack is not valid ZIP64: {error}"))?;

    let mut names = BTreeSet::new();
    let mut folded_names = BTreeSet::new();
    for index in 0..archive.len() {
        let entry = archive
            .by_index(index)
            .map_err(|error| format!("Could not inspect component pack entry: {error}"))?;
        let name = safe_archive_path(entry.name())?;
        let folded = name.to_lowercase();
        if !folded_names.insert(folded) {
            return Err(format!(
                "Native component pack contains a duplicate archive path: {name}"
            ));
        }
        if entry.is_dir() {
            return Err(format!(
                "Native component pack contains an unauthorized directory entry: {name}"
            ));
        }
        if let Some(mode) = entry.unix_mode() {
            if mode & 0o170000 == 0o120000 {
                return Err(format!(
                    "Native component pack contains a symbolic link: {name}"
                ));
            }
        }
        names.insert(name);
    }
    if !names.contains(MANIFEST_NAME) || !names.contains(SIGNATURE_NAME) {
        return Err("Native component pack is missing its manifest or signature.".to_string());
    }

    let manifest_bytes = read_small_entry(&mut archive, MANIFEST_NAME, MAX_MANIFEST_BYTES)?;
    let signature_text = read_small_entry(&mut archive, SIGNATURE_NAME, 256)?;
    let signature_bytes = BASE64
        .decode(
            std::str::from_utf8(&signature_text)
                .map_err(|_| "Component pack signature is not UTF-8.".to_string())?
                .trim(),
        )
        .map_err(|error| format!("Component pack signature is invalid base64: {error}"))?;
    let signature = Signature::from_slice(&signature_bytes)
        .map_err(|error| format!("Component pack signature has invalid length: {error}"))?;
    trust
        .public_key
        .verify(&manifest_bytes, &signature)
        .map_err(|_| "Native component pack manifest signature is invalid.".to_string())?;

    let manifest_value: Value = serde_json::from_slice(&manifest_bytes)
        .map_err(|error| format!("Native component pack manifest is invalid JSON: {error}"))?;
    if canonical_json(&manifest_value)?.as_bytes() != manifest_bytes {
        return Err("Native component pack manifest is not canonical JSON.".to_string());
    }
    let manifest: PackManifest = serde_json::from_value(manifest_value)
        .map_err(|error| format!("Native component pack manifest is malformed: {error}"))?;
    validate_manifest_identity(
        &manifest,
        trust,
        expected_component,
        expected_product_version,
        expected_compatible_core,
    )?;
    validate_component_contract(&manifest)?;

    let mut expected_names =
        BTreeSet::from([MANIFEST_NAME.to_string(), SIGNATURE_NAME.to_string()]);
    let mut manifest_paths = BTreeSet::new();
    let mut manifest_folded_paths = BTreeSet::new();
    let mut calculated_total = 0_u64;
    let mut verified_files = Vec::with_capacity(manifest.files.len());
    for item in &manifest.files {
        let safe_path = safe_relative_path(&item.path)?;
        if !manifest_paths.insert(safe_path.clone())
            || !manifest_folded_paths.insert(safe_path.to_lowercase())
        {
            return Err(format!(
                "Native component pack manifest contains a duplicate path: {safe_path}"
            ));
        }
        if !is_lower_hex_sha256(&item.sha256) {
            return Err(format!(
                "Native component pack manifest has invalid SHA-256 for {safe_path}"
            ));
        }
        calculated_total = calculated_total
            .checked_add(item.bytes)
            .ok_or_else(|| "Native component pack byte total overflowed.".to_string())?;
        expected_names.insert(format!("{PAYLOAD_PREFIX}{safe_path}"));
        verified_files.push(VerifiedPackFile {
            path: safe_path,
            bytes: item.bytes,
            sha256: item.sha256.clone(),
        });
    }
    if manifest.file_count != manifest.files.len() || manifest.total_bytes != calculated_total {
        return Err("Native component pack manifest count or byte total is invalid.".to_string());
    }
    let unexpected: Vec<_> = names.difference(&expected_names).cloned().collect();
    let missing: Vec<_> = expected_names.difference(&names).cloned().collect();
    if !unexpected.is_empty() {
        return Err(format!(
            "Native component pack contains unexpected entries: {}",
            unexpected.join(", ")
        ));
    }
    if !missing.is_empty() {
        return Err(format!(
            "Native component pack is missing entries: {}",
            missing.join(", ")
        ));
    }

    for item in &verified_files {
        verify_archive_payload(&mut archive, item)?;
    }
    Ok((
        VerifiedPack {
            path: path.to_path_buf(),
            sha256: pack_sha256,
            component: manifest.component,
            product_version: manifest.product_version,
            compatible_core: manifest.compatible_core,
            signing_key_id: manifest.signing_key_id,
            file_count: manifest.file_count,
            total_bytes: manifest.total_bytes,
            metadata: manifest.metadata,
            files: verified_files,
        },
        archive,
    ))
}

pub fn verify_and_extract_pack(
    path: &Path,
    destination: &Path,
    trust: &PackTrust,
    expected_component: Option<&str>,
    expected_product_version: Option<&str>,
    expected_compatible_core: Option<&str>,
) -> Result<VerifiedPack, String> {
    let (verified, mut archive) = open_and_verify_pack(
        path,
        trust,
        expected_component,
        expected_product_version,
        expected_compatible_core,
    )?;
    prepare_empty_destination(destination)?;
    for item in &verified.files {
        let archive_name = format!("{PAYLOAD_PREFIX}{}", item.path);
        let mut entry = archive
            .by_name(&archive_name)
            .map_err(|error| format!("Verified pack entry disappeared: {error}"))?;
        let target = destination.join(Path::new(&item.path));
        let parent = target
            .parent()
            .ok_or_else(|| format!("Pack entry has no destination parent: {}", item.path))?;
        ensure_beneath(destination, &target)?;
        fs::create_dir_all(parent)
            .map_err(|error| format!("Could not create pack destination directory: {error}"))?;
        reject_reparse_path(parent)?;
        let partial = target.with_extension(format!(
            "{}.partial",
            target
                .extension()
                .and_then(|value| value.to_str())
                .unwrap_or_default()
        ));
        let mut output = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&partial)
            .map_err(|error| format!("Could not create staged pack file: {error}"))?;
        let mut digest = Sha256::new();
        let mut observed = 0_u64;
        let mut buffer = vec![0_u8; 1024 * 1024];
        loop {
            let count = entry
                .read(&mut buffer)
                .map_err(|error| format!("Could not extract verified pack entry: {error}"))?;
            if count == 0 {
                break;
            }
            output
                .write_all(&buffer[..count])
                .map_err(|error| format!("Could not write staged pack entry: {error}"))?;
            digest.update(&buffer[..count]);
            observed += count as u64;
        }
        output
            .sync_all()
            .map_err(|error| format!("Could not flush staged pack entry: {error}"))?;
        drop(output);
        let observed_hash = format!("{:x}", digest.finalize());
        if observed != item.bytes || observed_hash != item.sha256 {
            let _ = fs::remove_file(&partial);
            return Err(format!(
                "Extracted pack entry failed byte verification: {}",
                item.path
            ));
        }
        fs::rename(&partial, &target)
            .map_err(|error| format!("Could not activate staged pack entry: {error}"))?;
    }
    verify_extracted_tree(destination, &verified.files)?;
    Ok(verified)
}

fn validate_manifest_identity(
    manifest: &PackManifest,
    trust: &PackTrust,
    expected_component: Option<&str>,
    expected_product_version: Option<&str>,
    expected_compatible_core: Option<&str>,
) -> Result<(), String> {
    if manifest.schema_version != PACK_SCHEMA_VERSION || manifest.product != PACK_PRODUCT {
        return Err("Native component pack schema or product identity is invalid.".to_string());
    }
    if manifest.signing_key_id != trust.key_id {
        return Err(
            "Native component pack signing key id does not match the embedded trust root."
                .to_string(),
        );
    }
    for (label, value) in [
        ("component", manifest.component.as_str()),
        ("product version", manifest.product_version.as_str()),
        ("compatible core", manifest.compatible_core.as_str()),
    ] {
        if value.is_empty() {
            return Err(format!("Native component pack {label} is empty."));
        }
    }
    if let Some(expected) = expected_component {
        if manifest.component != expected {
            return Err(format!(
                "Native component pack component mismatch: expected {expected}, got {}.",
                manifest.component
            ));
        }
    }
    if let Some(expected) = expected_product_version {
        if manifest.product_version != expected {
            return Err(format!(
                "Native component pack version mismatch: expected {expected}, got {}.",
                manifest.product_version
            ));
        }
    }
    if let Some(expected) = expected_compatible_core {
        if manifest.compatible_core != expected {
            return Err(format!(
                "Native component pack compatible core mismatch: expected {expected}, got {}.",
                manifest.compatible_core
            ));
        }
    }
    Ok(())
}

/// One caption model tier's complete, pinned file identity. Mirrors
/// `civiccast.native.caption_tiers.CaptionTierSpec`: `model_root` is the
/// payload-relative directory prefix (`models/<model_directory>`, no
/// trailing slash) this tier's files live under, and `files` maps a
/// filename (relative to `model_root`) to its exact `(bytes, sha256)`.
/// `pending` marks a placeholder tier whose identity is not yet
/// owner-bound (e.g. the floor tier before the R7-measurement binding);
/// a pending tier must never verify as present.
#[derive(Debug, Clone, Copy)]
struct CaptionTierSpec {
    model_root: &'static str,
    files: &'static [(&'static str, u64, &'static str)],
    pending: bool,
}

/// Mirrors `civiccast.native.caption_tiers.CAPTION_TIER_REGISTRY`: every
/// known caption tier's pinned identity. large-v3 re-uses the existing
/// pinned `CAPTION_MODEL_ROOT`/`CAPTION_MODEL_FILES` identity verbatim; the
/// floor entry is now BOUND to the owner's 2026-07-30 BINDING ruling
/// (`medium`, see `.agent-runs/native-windows/wp1-caption-integrity/
/// OWNER-DECISION-caption-adaptive-tier.md`) -- `pending: false`, a non-empty
/// pinned inventory, exactly the same shape large-v3 has always had.
fn caption_tier_registry() -> BTreeMap<&'static str, CaptionTierSpec> {
    BTreeMap::from([
        (
            LARGE_V3_TIER_ID,
            CaptionTierSpec {
                model_root: CAPTION_MODEL_ROOT,
                files: &CAPTION_MODEL_FILES,
                pending: false,
            },
        ),
        (
            FLOOR_TIER_ID,
            CaptionTierSpec {
                model_root: CAPTION_FLOOR_TIER_MODEL_ROOT,
                files: &CAPTION_FLOOR_TIER_MODEL_FILES,
                pending: false,
            },
        ),
    ])
}

/// The caption pack's declared per-tier inventory (`metadata.caption_tiers`,
/// a non-empty array of tier id strings). Mandatory for every caption pack:
/// the pack builder and this verifier always ship together in the same
/// signed candidate, so there is no supported path that pairs a pack built
/// without this declaration against this verifier.
fn required_caption_tier_ids(manifest: &PackManifest) -> Result<Vec<String>, String> {
    const MISSING_DECLARATION: &str =
        "caption pack metadata is missing its per-tier inventory declaration";
    let array = manifest
        .metadata
        .get("caption_tiers")
        .and_then(Value::as_array)
        .filter(|array| !array.is_empty())
        .ok_or_else(|| MISSING_DECLARATION.to_string())?;
    array
        .iter()
        .map(|item| item.as_str().map(str::to_string))
        .collect::<Option<Vec<String>>>()
        .ok_or_else(|| MISSING_DECLARATION.to_string())
}

/// Verify every REQUIRED caption tier against ITS OWN recorded inventory.
///
/// Mirrors `civiccast.installer.native_packs.verify_caption_pack_tiers`: the
/// fix for the R7-tester-surfaced defect where the old verifier hard-coded
/// large-v3's file inventory as THE required inventory for the whole
/// component, so any other tier -- with its own legitimately different file
/// set -- structurally failed, and a tier's files could be silently checked
/// against another tier's hashes instead of its own.
///
/// Fails closed on:
/// * a required tier absent from `present_tier_ids`;
/// * a declared tier that is unknown, or known but not yet owner-bound (no
///   tier in the real registry is pending anymore -- both large-v3 and the
///   floor tier are bound -- but the check stays load-bearing for any
///   future tier awaiting its own binding);
/// * extra files under a tier's `model_root` payload prefix beyond what
///   that tier's OWN inventory declares;
/// * a tier's OWN recorded inventory naming a file the pack does not
///   contain;
/// * any size/SHA-256 mismatch -- including bytes legitimately belonging to
///   a DIFFERENT tier being present under this tier's path (cross-tier file
///   borrowing).
///
/// `required_tier_ids` is supplied by the caller -- pack self-verification
/// (`validate_component_contract`) passes whatever the pack itself declares
/// present (a self-consistency check); a consumer requiring a specific tier
/// set would pass its own. The required set is never assumed inside this
/// function. Pure decision logic: takes already-parsed manifest data and a
/// registry value, touches no filesystem.
fn verify_caption_pack_tiers(
    files: &[PackManifestFile],
    present_tier_ids: &[String],
    required_tier_ids: &[String],
    registry: &BTreeMap<&'static str, CaptionTierSpec>,
) -> Result<BTreeSet<String>, String> {
    let present: BTreeSet<&str> = present_tier_ids.iter().map(String::as_str).collect();
    let required: BTreeSet<&str> = required_tier_ids.iter().map(String::as_str).collect();
    let missing_required: Vec<&str> = required.difference(&present).copied().collect();
    if !missing_required.is_empty() {
        return Err(format!(
            "caption pack is missing required tier(s): {}",
            missing_required.join(", ")
        ));
    }

    let files_by_path: BTreeMap<&str, &PackManifestFile> = files
        .iter()
        .map(|item| (item.path.as_str(), item))
        .collect();

    // Every DECLARED-present tier is verified, not only the required ones:
    // an unrequested tier the pack claims to carry is exactly as capable of
    // smuggling unreviewed bytes as a required one would be, so accepting it
    // unchecked would reopen the same hole this fix closes.
    let mut verified = BTreeSet::new();
    for tier_id in present_tier_ids {
        let spec = registry
            .get(tier_id.as_str())
            .ok_or_else(|| format!("caption pack declares an unknown tier: {tier_id:?}"))?;
        if spec.pending {
            return Err(format!(
                "caption pack tier {tier_id:?} is not yet bound to a pinned model identity \
                 (owner binding pending)"
            ));
        }

        let prefix = format!("{}/", spec.model_root);
        let mut observed: BTreeMap<&str, &PackManifestFile> = BTreeMap::new();
        for (&path, &entry) in &files_by_path {
            if let Some(relative) = path.strip_prefix(prefix.as_str()) {
                observed.insert(relative, entry);
            }
        }
        let expected_names: BTreeSet<&str> = spec.files.iter().map(|(name, _, _)| *name).collect();
        let observed_names: BTreeSet<&str> = observed.keys().copied().collect();
        let extra: Vec<&str> = observed_names
            .difference(&expected_names)
            .copied()
            .collect();
        if !extra.is_empty() {
            return Err(format!(
                "caption pack tier {tier_id:?} contains unexpected files: {}",
                extra.join(", ")
            ));
        }
        let missing: Vec<&str> = expected_names
            .difference(&observed_names)
            .copied()
            .collect();
        if !missing.is_empty() {
            return Err(format!(
                "caption pack tier {tier_id:?} is missing declared files: {}",
                missing.join(", ")
            ));
        }
        for (name, expected_bytes, expected_sha256) in spec.files {
            let entry = observed
                .get(name)
                .copied()
                .expect("declared file must be present after the missing-files check above");
            if entry.bytes != *expected_bytes || entry.sha256 != *expected_sha256 {
                return Err(format!(
                    "caption pack tier {tier_id:?} substituted unapproved bytes for {name}"
                ));
            }
        }
        verified.insert(tier_id.clone());
    }
    Ok(verified)
}

fn validate_component_contract(manifest: &PackManifest) -> Result<(), String> {
    if SOURCE_BOUND_COMPONENTS.contains(&manifest.component.as_str()) {
        let source_sha = manifest
            .metadata
            .get("source_sha")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                format!(
                    "{} pack metadata source SHA is missing or invalid",
                    manifest.component
                )
            })?;
        if source_sha.len() != 40
            || !source_sha
                .bytes()
                .all(|character| character.is_ascii_digit() || (b'a'..=b'f').contains(&character))
        {
            return Err(format!(
                "{} pack metadata source SHA is missing or invalid",
                manifest.component
            ));
        }
        if manifest.component == "native-app-payload"
            && manifest
                .metadata
                .get("civiccast_source_head")
                .and_then(Value::as_str)
                != Some(source_sha)
        {
            return Err(
                "native-app-payload source SHA does not match civiccast_source_head".to_string(),
            );
        }
    }
    if manifest.component == CAPTION_COMPONENT {
        // The activation self-test fixture is pack-wide (not tied to any one
        // tier's model directory), so it is checked before per-tier
        // inventories -- and before requiring the `caption_tiers` metadata
        // key, so a manifest missing both fails on the same "large-v3"
        // self-test defect it always did, rather than a confusing new error.
        // (Mirrors the ordering in
        // `civiccast.installer.native_packs._validate_component_contract`.)
        require_pinned_manifest_file(
            manifest,
            CAPTION_SELF_TEST_PATH,
            CAPTION_SELF_TEST_BYTES,
            CAPTION_SELF_TEST_SHA256,
        )?;
        let present_tier_ids = required_caption_tier_ids(manifest)?;
        // Self-consistency check: whatever tiers this pack CLAIMS to carry
        // (`present_tier_ids`) must be structurally complete and correct
        // against THEIR OWN recorded inventories -- never large-v3's,
        // regardless of which tier is being checked (the defect this fixes).
        // `caption_tiers` is mandatory for every pack (no legacy exemption:
        // the pack builder and this verifier always ship together in the
        // same signed candidate, so no supported path ever pairs an
        // old-format pack with this verifier).
        verify_caption_pack_tiers(
            &manifest.files,
            &present_tier_ids,
            &present_tier_ids,
            &caption_tier_registry(),
        )?;
        for (key, expected) in [
            ("model_architecture", "large-v3"),
            ("model_directory", "faster-whisper-large-v3"),
            ("model_repository", CAPTION_LARGE_TIER_MODEL_REPOSITORY),
            ("model_revision", CAPTION_LARGE_TIER_MODEL_REVISION),
            ("runtime_backend", "faster-whisper"),
            ("runtime_version", "1.2.1"),
            ("ctranslate2_version", "4.8.1"),
            ("runtime_device", "cpu"),
            ("runtime_compute_type", "int8"),
            ("self_test_audio_file", "jfk.wav"),
            ("self_test_audio_sha256", CAPTION_SELF_TEST_SHA256),
            ("self_test_expected_phrase", "and so my fellow americans"),
        ] {
            if manifest.metadata.get(key).and_then(Value::as_str) != Some(expected) {
                return Err(format!(
                    "Mandatory large-v3 caption pack metadata mismatch: {key}"
                ));
            }
        }
        if manifest.metadata.get("component").and_then(Value::as_str) != Some(CAPTION_COMPONENT)
            || manifest.metadata.get("required").and_then(Value::as_bool) != Some(true)
            || manifest
                .metadata
                .get("hardware_acceleration_required")
                .and_then(Value::as_bool)
                != Some(false)
            || manifest
                .metadata
                .get("self_test_audio_bytes")
                .and_then(Value::as_u64)
                != Some(CAPTION_SELF_TEST_BYTES)
        {
            return Err(
                "Mandatory large-v3 caption pack metadata identity is incomplete.".to_string(),
            );
        }
        validate_caption_model_metadata(manifest)?;
        if manifest.files.iter().any(|item| {
            item.path.starts_with("runtime/")
                || item.path.contains("ggml-")
                || item.path.ends_with("whisper-cli.exe")
        }) {
            return Err(
                "Mandatory caption pack contains a rejected alternate runtime or quantized model."
                    .to_string(),
            );
        }
    }
    if manifest.component == CAPTION_FLOOR_COMPONENT {
        // <installer-path-audit BL-08> THE MANDATORY CAPTION FLOOR MODEL HAD
        // NO CONTENT CONTRACT AT ALL.
        //
        // Before this branch, `validate_component_contract` had exactly three:
        // SOURCE_BOUND_COMPONENTS, `component == CAPTION_COMPONENT`
        // ("captions-large-v3"), and the three Ollama ids. A grep for
        // "captions-floor" in this file found NONE -- the owner-designated
        // MANDATORY caption baseline hit no branch whatsoever.
        //
        // The pinned constants existed and were unreachable on this path:
        // CAPTION_FLOOR_TIER_MODEL_FILES is only reachable through
        // `verify_caption_pack_tiers`, whose sole production caller was inside
        // the large-v3 branch above and passes `present_tier_ids` as BOTH the
        // present and the required set -- a self-consistency check a
        // large-v3-only pack satisfies trivially. Stack PR #127 on top and
        // `pack_identity_expectations` returns (None, None) for
        // `captions-floor` on a station index, so the declared version pair is
        // not compared either. The COMPLETE set of machine checks on the
        // mandatory caption model was therefore: trust-root signature,
        // `component == "captions-floor"`, outer SHA-256 == the index's, and
        // byte count == the index's -- **every one of them a value the
        // publisher chose and signed in one operation**. The builder concedes
        // it in `scripts/build_native_station_bundle.py`'s own comment, and
        // `--captions-floor-root` takes whatever directory it is pointed at
        // with no hash pin.
        //
        // The residual gate is not one: `main.rs`'s live inference self-test
        // only checks that `jfk.wav` transcribes to "and so my fellow
        // americans", which `small`, `base` and most quantizations also do,
        // and `station-set.json` records nothing about which floor model
        // landed. So a release engineer pointing `--captions-floor-root` at
        // `faster-whisper-small` shipped an unreviewed ASR model to every
        // station in the fleet, on the legally non-negotiable captions path,
        // with every gate green.
        //
        // This is the large-v3 branch minus the metadata block: the tier's own
        // pinned inventory (by NAME, SIZE and SHA-256, per file) plus the
        // pack-wide self-test fixture. Every constant already existed.
        //
        // DEVELOPMENT-SIGNED PACKS ARE EXEMPT, and that is not a hole. The
        // pinned inventory names a real 1.5 GB `model.bin` by SHA-256, so a
        // pack carrying it cannot be synthesised -- which is why the fixtures
        // that round-trip a whole signed station bundle sign with a
        // `development-` key id. Such a pack is unusable on a shipping build
        // regardless: `embedded_pack_trust` refuses a bootstrap whose embedded
        // key id starts with `development-` unless it was compiled with
        // `CIVICCAST_ALLOW_DEVELOPMENT_PACK_KEY=1`, and the pack's signature
        // must verify against THAT bootstrap's key. So the exemption cannot
        // widen any path a station takes; it is the same non-release boundary
        // the trust root already draws. A release-signed floor pack with
        // substituted bytes is refused -- see
        // `a_release_signed_floor_pack_with_substituted_model_bytes_is_refused`.
        if manifest.signing_key_id.starts_with("development-") {
            return Ok(());
        }
        require_pinned_manifest_file(
            manifest,
            CAPTION_SELF_TEST_PATH,
            CAPTION_SELF_TEST_BYTES,
            CAPTION_SELF_TEST_SHA256,
        )?;
        // Deliberately NOT `required_caption_tier_ids(manifest)` on both
        // sides: passing a pack's OWN declaration as its own requirement is
        // the self-consistency shape that let the floor model through
        // untouched. The required set is this binary's constant.
        let required = [FLOOR_TIER_ID.to_string()];
        verify_caption_pack_tiers(
            &manifest.files,
            &required,
            &required,
            &caption_tier_registry(),
        )?;
    }
    if manifest.component == WHISTLE_COMPONENT {
        validate_whistle_pack_contract(manifest)?;
    }
    if matches!(
        manifest.component.as_str(),
        "summary-gemma4-12b" | "summary-gemma4-e4b" | "translation-translategemma-4b"
    ) {
        validate_ollama_model_contract(manifest)?;
    }
    Ok(())
}

fn validate_whistle_pack_contract(manifest: &PackManifest) -> Result<(), String> {
    let expected_metadata: BTreeMap<String, Value> = serde_json::from_value(serde_json::json!({
        "component": WHISTLE_COMPONENT,
        "required": true,
        "model_name": "whistle",
        "model_source_url": "https://huggingface.co/Cactus-Compute/whistle/resolve/main/whistle.cact",
        "model_bytes": 16919407_u64,
        "model_sha256": WHISTLE_MODEL_SHA256,
        "model_license": "Apache-2.0",
        "engine_distribution": "cactus-needle",
        "engine_version": "3.1.0",
        "engine_wheel_source_url": "https://huggingface.co/Cactus-Compute/needle3/resolve/main/python/cactus_needle-3.1.0-py3-none-win_amd64.whl",
        "engine_wheel_sha256": WHISTLE_ENGINE_WHEEL_SHA256,
        "engine_library": "libneedle.dll",
        "engine_library_bytes": 1502720_u64,
        "engine_library_sha256": WHISTLE_ENGINE_DLL_SHA256,
        "engine_license": "Apache-2.0"
    }))
    .map_err(|error| format!("Whistle pack contract is invalid: {error}"))?;
    if manifest.metadata != expected_metadata {
        return Err(
            "Whistle caption pack metadata does not match its reviewed contract".to_string(),
        );
    }

    const EXPECTED_FILES: [(&str, u64, &str); 2] = [
        ("libneedle.dll", 1_502_720, WHISTLE_ENGINE_DLL_SHA256),
        ("whistle.cact", 16_919_407, WHISTLE_MODEL_SHA256),
    ];
    if manifest.files.len() != EXPECTED_FILES.len() {
        return Err(
            "Whistle caption pack payload must contain exactly its two reviewed files".to_string(),
        );
    }
    for (path, bytes, sha256) in EXPECTED_FILES {
        require_pinned_manifest_file(manifest, path, bytes, sha256)?;
    }
    Ok(())
}

fn validate_caption_model_metadata(manifest: &PackManifest) -> Result<(), String> {
    let model_files = manifest
        .metadata
        .get("model_files")
        .and_then(Value::as_object)
        .ok_or_else(|| {
            "Mandatory large-v3 caption pack model_files metadata is missing.".to_string()
        })?;
    if model_files.len() != CAPTION_MODEL_FILES.len() {
        return Err(
            "Mandatory large-v3 caption pack model_files metadata has the wrong file set."
                .to_string(),
        );
    }
    for (name, bytes, sha256) in CAPTION_MODEL_FILES {
        let entry = model_files
            .get(name)
            .and_then(Value::as_object)
            .ok_or_else(|| {
                format!("Mandatory large-v3 caption pack model_files metadata is missing {name}.")
            })?;
        if entry.len() != 2
            || entry.get("bytes").and_then(Value::as_u64) != Some(bytes)
            || entry.get("sha256").and_then(Value::as_str) != Some(sha256)
        {
            return Err(format!(
                "Mandatory large-v3 caption pack model_files metadata mismatch: {name}"
            ));
        }
    }
    Ok(())
}

/// Parses and identity-checks the embedded reviewed Ollama model lock
/// (`OLLAMA_MODEL_LOCK_JSON`) -- the SINGLE parse site both
/// [`validate_ollama_model_contract`] (the signed-pack verification path) and
/// [`reviewed_ollama_model`] (the direct-registry acquisition path in
/// `component_acquisition.rs`/`acquisition_catalog.rs`) build on, so the two
/// pull protocols can never read two different copies of the same pin.
fn load_ollama_model_lock() -> Result<OllamaModelLock, String> {
    let lock: OllamaModelLock = serde_json::from_str(OLLAMA_MODEL_LOCK_JSON)
        .map_err(|error| format!("Embedded reviewed model lock is invalid: {error}"))?;
    if lock.schema_version != 1
        || lock.registry != "registry.ollama.ai"
        || lock.ollama_runtime_version != "0.30.6"
    {
        return Err("Embedded reviewed model lock identity is invalid.".to_string());
    }
    Ok(lock)
}

/// One pinned Ollama blob identity (config or a layer): `bytes`/`sha256` from
/// the embedded reviewed lock. Content-addressed -- `sha256` IS the digest
/// the acquisition engine requests the blob under AND the hash it verifies
/// the downloaded bytes against, never a separately pinned outer hash.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ReviewedOllamaBlob {
    pub(crate) bytes: u64,
    pub(crate) sha256: String,
}

/// The reviewed acquisition identity for one Ollama model, read out of the
/// SAME embedded lock [`validate_ollama_model_contract`] already pins the
/// signed-pack path against -- never a second, hand-transcribed copy of
/// these digests. Consumed by `component_acquisition`/`acquisition_catalog`
/// to build the direct-registry download catalog entry for `local_ai_model`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ReviewedOllamaModel {
    pub(crate) registry: String,
    pub(crate) repository: String,
    pub(crate) tag: String,
    pub(crate) manifest_bytes: u64,
    pub(crate) manifest_sha256: String,
    pub(crate) config: ReviewedOllamaBlob,
    pub(crate) layers: Vec<ReviewedOllamaBlob>,
}

/// Looks up `model_name` (the lock's own key, e.g. `"gemma4-12b"`) in the
/// embedded reviewed Ollama model lock and returns its acquisition identity.
/// `Err` only when the lock itself is malformed or the model is absent --
/// both impossible in a real build for a name this crate hardcodes itself
/// (see `acquisition_catalog.rs`'s own test asserting this lookup succeeds
/// for `"gemma4-12b"`), the same "impossible but checked" posture
/// `embedded_pack_trust` already uses elsewhere in this file.
pub(crate) fn reviewed_ollama_model(model_name: &str) -> Result<ReviewedOllamaModel, String> {
    let lock = load_ollama_model_lock()?;
    let entry = lock.models.get(model_name).ok_or_else(|| {
        format!("Native model pack is not present in the reviewed model lock: {model_name}")
    })?;
    Ok(ReviewedOllamaModel {
        registry: lock.registry.clone(),
        repository: entry.repository.clone(),
        tag: entry.tag.clone(),
        manifest_bytes: entry.manifest_bytes,
        manifest_sha256: entry.manifest_sha256.clone(),
        config: ReviewedOllamaBlob {
            bytes: entry.config.bytes,
            sha256: entry.config.sha256.clone(),
        },
        layers: entry
            .layers
            .iter()
            .map(|layer| ReviewedOllamaBlob {
                bytes: layer.bytes,
                sha256: layer.sha256.clone(),
            })
            .collect(),
    })
}

fn validate_ollama_model_contract(manifest: &PackManifest) -> Result<(), String> {
    let lock = load_ollama_model_lock()?;
    let model_name = manifest
        .metadata
        .get("model_name")
        .and_then(Value::as_str)
        .ok_or_else(|| "Native model pack is missing model_name metadata.".to_string())?;
    let reviewed = lock.models.get(model_name).ok_or_else(|| {
        format!("Native model pack is not present in the reviewed model lock: {model_name}")
    })?;
    if reviewed.component != manifest.component
        || manifest
            .metadata
            .get("manifest_sha256")
            .and_then(Value::as_str)
            != Some(reviewed.manifest_sha256.as_str())
        || manifest
            .metadata
            .get("ollama_runtime_version")
            .and_then(Value::as_str)
            != Some(lock.ollama_runtime_version.as_str())
    {
        return Err(format!(
            "Native model pack metadata differs from the reviewed model lock: {model_name}"
        ));
    }

    let mut expected_paths = BTreeSet::new();
    expected_paths.insert("MODEL-PROVENANCE.json".to_string());
    let manifest_path = format!(
        "manifests/{}/library/{}/{}",
        lock.registry, reviewed.repository, reviewed.tag
    );
    require_reviewed_model_file(
        manifest,
        &manifest_path,
        reviewed.manifest_bytes,
        &reviewed.manifest_sha256,
    )?;
    expected_paths.insert(manifest_path);
    let config_path = format!("blobs/sha256-{}", reviewed.config.sha256);
    require_reviewed_model_file(
        manifest,
        &config_path,
        reviewed.config.bytes,
        &reviewed.config.sha256,
    )?;
    expected_paths.insert(config_path);
    for layer in &reviewed.layers {
        if !matches!(
            layer.media_type.as_str(),
            "application/vnd.ollama.image.model"
                | "application/vnd.ollama.image.projector"
                | "application/vnd.ollama.image.license"
                | "application/vnd.ollama.image.params"
                | "application/vnd.ollama.image.template"
        ) {
            return Err(format!(
                "Embedded reviewed model lock has an invalid layer media type: {}",
                layer.media_type
            ));
        }
        let path = format!("blobs/sha256-{}", layer.sha256);
        require_reviewed_model_file(manifest, &path, layer.bytes, &layer.sha256)?;
        expected_paths.insert(path);
    }
    let actual_paths: BTreeSet<_> = manifest
        .files
        .iter()
        .map(|file| file.path.clone())
        .collect();
    if actual_paths != expected_paths {
        return Err(format!(
            "Native model pack inventory differs from the reviewed model lock: {model_name}"
        ));
    }
    let provenance = manifest
        .files
        .iter()
        .find(|file| file.path == "MODEL-PROVENANCE.json")
        .ok_or_else(|| "Native model pack is missing MODEL-PROVENANCE.json.".to_string())?;
    if provenance.bytes == 0 || !is_lower_hex_sha256(&provenance.sha256) {
        return Err("Native model pack provenance identity is invalid.".to_string());
    }
    Ok(())
}

fn require_reviewed_model_file(
    manifest: &PackManifest,
    path: &str,
    expected_bytes: u64,
    expected_sha256: &str,
) -> Result<(), String> {
    let item = manifest
        .files
        .iter()
        .find(|item| item.path == path)
        .ok_or_else(|| format!("Native model pack is missing reviewed model lock file: {path}"))?;
    if item.bytes != expected_bytes || item.sha256 != expected_sha256 {
        return Err(format!(
            "Native model pack substituted bytes outside the reviewed model lock: {path}"
        ));
    }
    Ok(())
}

fn require_pinned_manifest_file(
    manifest: &PackManifest,
    path: &str,
    expected_bytes: u64,
    expected_sha256: &str,
) -> Result<(), String> {
    // <installer-path-audit BL-08> The component is named from the manifest
    // rather than hardcoded to "large-v3": this helper now serves the
    // mandatory FLOOR pack too, and a refusal that names the wrong component
    // sends a support case to the wrong pack.
    let component = manifest.component.as_str();
    let item = manifest
        .files
        .iter()
        .find(|item| item.path == path)
        .ok_or_else(|| format!("Mandatory {component} caption pack is missing {path}."))?;
    if item.bytes != expected_bytes || item.sha256 != expected_sha256 {
        return Err(format!(
            "Mandatory {component} caption pack substituted unapproved bytes for {path}."
        ));
    }
    Ok(())
}

fn verify_archive_payload(
    archive: &mut ZipArchive<File>,
    item: &VerifiedPackFile,
) -> Result<(), String> {
    let archive_name = format!("{PAYLOAD_PREFIX}{}", item.path);
    let mut entry = archive
        .by_name(&archive_name)
        .map_err(|error| format!("Could not read component pack entry: {error}"))?;
    if entry.compression() != CompressionMethod::Stored {
        return Err(format!(
            "Native component pack entry is not stored encoding: {}",
            item.path
        ));
    }
    if entry.size() != item.bytes {
        return Err(format!(
            "Native component pack entry size mismatch: {}",
            item.path
        ));
    }
    let mut digest = Sha256::new();
    let mut observed = 0_u64;
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let count = entry
            .read(&mut buffer)
            .map_err(|error| format!("Could not hash component pack entry: {error}"))?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
        observed += count as u64;
    }
    let hash = format!("{:x}", digest.finalize());
    if observed != item.bytes {
        return Err(format!(
            "Native component pack entry size mismatch: {}",
            item.path
        ));
    }
    if hash != item.sha256 {
        return Err(format!(
            "Native component pack entry SHA-256 mismatch: {}",
            item.path
        ));
    }
    Ok(())
}

fn read_small_entry(
    archive: &mut ZipArchive<File>,
    name: &str,
    maximum: u64,
) -> Result<Vec<u8>, String> {
    let mut entry = archive
        .by_name(name)
        .map_err(|error| format!("Could not read {name}: {error}"))?;
    if entry.size() > maximum {
        return Err(format!("Native component pack {name} is too large."));
    }
    let mut output = Vec::with_capacity(entry.size() as usize);
    entry
        .read_to_end(&mut output)
        .map_err(|error| format!("Could not read {name}: {error}"))?;
    Ok(output)
}

pub(crate) fn canonical_json(value: &Value) -> Result<String, String> {
    let mut output = String::new();
    write_canonical_json(value, &mut output)?;
    output.push('\n');
    Ok(output)
}

fn write_canonical_json(value: &Value, output: &mut String) -> Result<(), String> {
    match value {
        Value::Null => output.push_str("null"),
        Value::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
        Value::Number(value) => output.push_str(&value.to_string()),
        Value::String(value) => output.push_str(
            &serde_json::to_string(value)
                .map_err(|error| format!("Could not canonicalize JSON string: {error}"))?,
        ),
        Value::Array(values) => {
            output.push('[');
            for (index, item) in values.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                write_canonical_json(item, output)?;
            }
            output.push(']');
        }
        Value::Object(values) => {
            output.push('{');
            let mut keys: Vec<_> = values.keys().collect();
            keys.sort();
            for (index, key) in keys.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                output.push_str(
                    &serde_json::to_string(key)
                        .map_err(|error| format!("Could not canonicalize JSON key: {error}"))?,
                );
                output.push(':');
                write_canonical_json(&values[*key], output)?;
            }
            output.push('}');
        }
    }
    Ok(())
}

fn safe_archive_path(value: &str) -> Result<String, String> {
    if value == MANIFEST_NAME || value == SIGNATURE_NAME {
        return Ok(value.to_string());
    }
    let relative = value
        .strip_prefix(PAYLOAD_PREFIX)
        .ok_or_else(|| format!("Unsafe native component pack archive path: {value:?}"))?;
    Ok(format!("{PAYLOAD_PREFIX}{}", safe_relative_path(relative)?))
}

pub(crate) fn safe_relative_path(value: &str) -> Result<String, String> {
    if value.is_empty()
        || !value.is_ascii()
        || value.starts_with('/')
        || value.contains('\\')
        || value.contains(':')
        || value.contains('\0')
    {
        return Err(format!("Unsafe native component pack path: {value:?}"));
    }
    for part in value.split('/') {
        let stem = part
            .split('.')
            .next()
            .unwrap_or_default()
            .to_ascii_lowercase();
        let reserved = matches!(stem.as_str(), "aux" | "clock$" | "con" | "nul" | "prn")
            || stem
                .strip_prefix("com")
                .and_then(|suffix| suffix.parse::<u8>().ok())
                .is_some_and(|number| (1..=9).contains(&number))
            || stem
                .strip_prefix("lpt")
                .and_then(|suffix| suffix.parse::<u8>().ok())
                .is_some_and(|number| (1..=9).contains(&number));
        if part.is_empty()
            || part == "."
            || part == ".."
            || part.ends_with([' ', '.'])
            || part
                .bytes()
                .any(|byte| byte < 32 || matches!(byte, b'<' | b'>' | b'"' | b'|' | b'?' | b'*'))
            || reserved
        {
            return Err(format!("Unsafe native component pack path: {value:?}"));
        }
    }
    Ok(value.to_string())
}

fn is_lower_hex_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

pub(crate) fn open_pack_file(path: &Path) -> Result<File, String> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_SHARE_READ: u32 = 0x0000_0001;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        options
            .share_mode(FILE_SHARE_READ)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    }
    let file = options
        .open(path)
        .map_err(|error| format!("Could not open native component pack: {error}"))?;
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::fs::MetadataExt;
        let metadata = file
            .metadata()
            .map_err(|error| format!("Could not inspect native component pack: {error}"))?;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err(format!(
                "Reparse points are not allowed for native component packs: {}",
                path.display()
            ));
        }
    }
    Ok(file)
}

pub(crate) fn sha256_reader(input: &mut File, label: &str) -> Result<String, String> {
    let mut digest = Sha256::new();
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let count = input
            .read(&mut buffer)
            .map_err(|error| format!("Could not hash {label}: {error}"))?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let mut input =
        File::open(path).map_err(|error| format!("Could not hash component pack: {error}"))?;
    sha256_reader(&mut input, "component pack")
}

fn prepare_empty_destination(destination: &Path) -> Result<(), String> {
    if destination.exists() {
        reject_reparse_path(destination)?;
        if !destination.is_dir() {
            return Err(format!(
                "Pack destination is not a directory: {}",
                destination.display()
            ));
        }
        if fs::read_dir(destination)
            .map_err(|error| format!("Could not inspect pack destination: {error}"))?
            .next()
            .is_some()
        {
            return Err(format!(
                "Pack destination must be empty: {}",
                destination.display()
            ));
        }
    } else {
        fs::create_dir_all(destination)
            .map_err(|error| format!("Could not create pack destination: {error}"))?;
    }
    Ok(())
}

fn ensure_beneath(root: &Path, target: &Path) -> Result<(), String> {
    let root = root
        .canonicalize()
        .map_err(|error| format!("Could not resolve pack destination: {error}"))?;
    let parent = target
        .parent()
        .ok_or_else(|| "Pack target has no parent.".to_string())?;
    let mut existing = parent;
    while !existing.exists() {
        existing = existing
            .parent()
            .ok_or_else(|| "Pack target escaped its destination.".to_string())?;
    }
    let existing = existing
        .canonicalize()
        .map_err(|error| format!("Could not resolve pack target parent: {error}"))?;
    if !existing.starts_with(&root) {
        return Err("Pack target escaped its destination.".to_string());
    }
    Ok(())
}

pub(crate) fn verify_extracted_tree(root: &Path, files: &[VerifiedPackFile]) -> Result<(), String> {
    let mut expected = BTreeSet::new();
    for item in files {
        let path = root.join(Path::new(&item.path));
        ensure_beneath(root, &path)?;
        reject_reparse_path(&path)?;
        let metadata = path
            .metadata()
            .map_err(|error| format!("Extracted pack file is missing: {error}"))?;
        if metadata.len() != item.bytes || sha256_file(&path)? != item.sha256 {
            return Err(format!(
                "Extracted pack file failed final verification: {}",
                item.path
            ));
        }
        expected.insert(path);
    }
    let mut stack = vec![root.to_path_buf()];
    while let Some(directory) = stack.pop() {
        for entry in fs::read_dir(&directory)
            .map_err(|error| format!("Could not inventory extracted pack: {error}"))?
        {
            let entry =
                entry.map_err(|error| format!("Could not inventory extracted pack: {error}"))?;
            let path = entry.path();
            reject_reparse_path(&path)?;
            if path.is_dir() {
                stack.push(path);
            } else if !expected.contains(&path) {
                return Err(format!(
                    "Extracted pack contains an unexpected file: {}",
                    path.display()
                ));
            }
        }
    }
    Ok(())
}

fn reject_reparse_path(path: &Path) -> Result<(), String> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("Could not inspect path {}: {error}", path.display()))?;
    if metadata.file_type().is_symlink() {
        return Err(format!(
            "Symbolic links are not allowed in native pack paths: {}",
            path.display()
        ));
    }
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err(format!(
                "Reparse points are not allowed in native pack paths: {}",
                path.display()
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        canonical_json, caption_tier_registry, open_pack_file, reviewed_ollama_model,
        safe_archive_path, safe_relative_path, validate_component_contract,
        validate_manifest_identity, validate_ollama_model_contract, validate_whistle_pack_contract,
        verify_caption_pack_tiers, CaptionTierSpec, PackManifest, PackManifestFile, PackTrust,
        CAPTION_COMPONENT,
        CAPTION_FLOOR_TIER_MODEL_FILES, CAPTION_FLOOR_TIER_MODEL_REPOSITORY,
        CAPTION_FLOOR_TIER_MODEL_REVISION, CAPTION_FLOOR_TIER_MODEL_ROOT, CAPTION_MODEL_FILES,
        CAPTION_MODEL_ROOT, CAPTION_NO_TIER_FILES, CAPTION_SELF_TEST_BYTES,
        CAPTION_SELF_TEST_PATH, CAPTION_SELF_TEST_SHA256, FLOOR_TIER_ID, LARGE_V3_TIER_ID,
        PACK_PRODUCT, WHISTLE_COMPONENT, WHISTLE_ENGINE_DLL_SHA256, WHISTLE_ENGINE_WHEEL_SHA256,
        WHISTLE_MODEL_SHA256,
    };
    use ed25519_dalek::SigningKey;
    use serde_json::{json, Value};
    use std::collections::{BTreeMap, BTreeSet};

    #[test]
    fn canonical_json_sorts_every_object_level() {
        let value = json!({"z": {"b": 2, "a": 1}, "a": [true, "x"]});
        assert_eq!(
            canonical_json(&value).expect("canonical JSON"),
            "{\"a\":[true,\"x\"],\"z\":{\"a\":1,\"b\":2}}\n"
        );
    }

    #[test]
    fn pack_paths_reject_traversal_absolute_ads_and_backslashes() {
        for unsafe_path in [
            "../escape",
            "a/../escape",
            "/absolute",
            "C:/drive",
            "a\\windows",
            "a//empty",
            "CON",
            "models/AUX.bin",
            "models/model.bin.",
            "models/model.bin ",
            "models/model?.bin",
            "models/control\u{1f}.bin",
            "models/caf\u{e9}.bin",
        ] {
            assert!(safe_relative_path(unsafe_path).is_err());
        }
        assert!(safe_archive_path("outside/file").is_err());
        assert_eq!(
            safe_archive_path("payload/runtime/tool.exe").expect("safe path"),
            "payload/runtime/tool.exe"
        );
    }

    #[test]
    fn pack_identity_binds_the_compatible_core() {
        let signing_key = SigningKey::from_bytes(&[7_u8; 32]);
        let trust = PackTrust {
            key_id: "test-key".to_string(),
            public_key: signing_key.verifying_key(),
        };
        let manifest = PackManifest {
            schema_version: 1,
            product: "civiccast-native".to_string(),
            component: "core".to_string(),
            product_version: "1.0.0-rc15".to_string(),
            compatible_core: "different-core".to_string(),
            signing_key_id: "test-key".to_string(),
            file_count: 0,
            total_bytes: 0,
            files: vec![],
            metadata: BTreeMap::new(),
        };

        assert!(validate_manifest_identity(
            &manifest,
            &trust,
            Some("core"),
            Some("1.0.0-rc15"),
            Some("1.0.0-rc15"),
        )
        .expect_err("incompatible core must fail")
        .contains("compatible core mismatch"));
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn opened_pack_handle_denies_replacement_until_verification_finishes() {
        let root =
            std::env::temp_dir().join(format!("civiccast-native-pack-lock-{}", std::process::id()));
        let source = root.join("source.ccpack");
        let replacement = root.join("replacement.ccpack");
        let moved = root.join("moved.ccpack");
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).expect("create pack-lock test directory");
        std::fs::write(&source, b"source").expect("write source");
        std::fs::write(&replacement, b"replacement").expect("write replacement");

        let handle = open_pack_file(&source).expect("open locked pack handle");
        assert!(
            std::fs::rename(&source, &moved).is_err(),
            "an open verifier handle must deny pack replacement"
        );
        drop(handle);

        std::fs::rename(&source, &moved).expect("replacement allowed after verifier closes");
        std::fs::remove_dir_all(&root).expect("clean pack-lock test directory");
    }

    #[test]
    fn caption_contract_rejects_a_signed_smaller_model_substitution() {
        let model_files = serde_json::json!({
            "README.md": {
                "bytes": 2052,
                "sha256": "39e96252229f5a3d0141dc81afb65a36fd205461ac21e5b70f2cd1248ef0082c"
            },
            "config.json": {
                "bytes": 2394,
                "sha256": "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9"
            },
            "model.bin": {
                "bytes": 3087284237_u64,
                "sha256": "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1"
            },
            "preprocessor_config.json": {
                "bytes": 340,
                "sha256": "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"
            },
            "tokenizer.json": {
                "bytes": 2480617,
                "sha256": "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca"
            },
            "vocabulary.json": {
                "bytes": 1068114,
                "sha256": "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"
            }
        });
        let metadata: BTreeMap<String, Value> = serde_json::from_value(serde_json::json!({
            "component": "captions-large-v3",
            "required": true,
            "model_architecture": "large-v3",
            "model_directory": "faster-whisper-large-v3",
            "model_repository": "Systran/faster-whisper-large-v3",
            "model_revision": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
            "model_files": model_files,
            "runtime_backend": "faster-whisper",
            "runtime_version": "1.2.1",
            "ctranslate2_version": "4.8.1",
            "runtime_device": "cpu",
            "runtime_compute_type": "int8",
            "hardware_acceleration_required": false,
            "self_test_audio_file": "jfk.wav",
            "self_test_audio_bytes": 352078,
            "self_test_audio_sha256": CAPTION_SELF_TEST_SHA256,
            "self_test_expected_phrase": "and so my fellow americans",
            "caption_tiers": [LARGE_V3_TIER_ID]
        }))
        .expect("caption metadata");
        let model_path = "models/faster-whisper-large-v3/model.bin";
        let model_bytes = 3_087_284_237;
        let model_sha256 = "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1";
        let mut manifest = PackManifest {
            schema_version: 1,
            product: "civiccast-native".to_string(),
            component: CAPTION_COMPONENT.to_string(),
            product_version: "1.0.0-rc15".to_string(),
            compatible_core: "1.0.0-rc15".to_string(),
            signing_key_id: "test".to_string(),
            file_count: 2,
            total_bytes: model_bytes,
            files: vec![PackManifestFile {
                path: model_path.to_string(),
                bytes: model_bytes,
                sha256: model_sha256.to_string(),
            }],
            metadata,
        };
        // The self-test fixture check runs before per-tier inventory
        // verification (see the ordering comment in
        // `validate_component_contract`), so an incomplete manifest that is
        // ALSO missing the self-test file fails on that check first,
        // regardless of how incomplete its tier file set is.
        assert!(validate_component_contract(&manifest)
            .expect_err("caption contract without real audio self-test must fail")
            .contains("self-test/jfk.wav"));

        for (path, bytes, sha256) in [
            (
                "models/faster-whisper-large-v3/README.md",
                2_052,
                "39e96252229f5a3d0141dc81afb65a36fd205461ac21e5b70f2cd1248ef0082c",
            ),
            (
                "models/faster-whisper-large-v3/config.json",
                2_394,
                "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9",
            ),
            (
                "models/faster-whisper-large-v3/preprocessor_config.json",
                340,
                "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
            ),
            (
                "models/faster-whisper-large-v3/tokenizer.json",
                2_480_617,
                "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca",
            ),
            (
                "models/faster-whisper-large-v3/vocabulary.json",
                1_068_114,
                "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
            ),
        ] {
            manifest.files.push(PackManifestFile {
                path: path.to_string(),
                bytes,
                sha256: sha256.to_string(),
            });
            manifest.file_count += 1;
            manifest.total_bytes += bytes;
        }

        manifest.files.push(PackManifestFile {
            path: CAPTION_SELF_TEST_PATH.to_string(),
            bytes: CAPTION_SELF_TEST_BYTES,
            sha256: CAPTION_SELF_TEST_SHA256.to_string(),
        });
        manifest.file_count += 1;
        manifest.total_bytes += CAPTION_SELF_TEST_BYTES;
        validate_component_contract(&manifest).expect("pinned caption contract");

        manifest.metadata.insert(
            "hardware_acceleration_required".to_string(),
            Value::Bool(true),
        );
        assert!(validate_component_contract(&manifest)
            .expect_err("mandatory caption pack must remain CPU portable")
            .contains("metadata identity"));
        manifest.metadata.insert(
            "hardware_acceleration_required".to_string(),
            Value::Bool(false),
        );

        manifest.files[0].bytes = 10;
        assert!(validate_component_contract(&manifest)
            .expect_err("smaller model must fail")
            .contains("substituted"));
    }

    #[test]
    fn ollama_model_contract_is_bound_to_the_embedded_reviewed_lock() {
        let metadata: BTreeMap<String, Value> = [
            ("model_name", "gemma4-e4b"),
            (
                "manifest_sha256",
                "c6eb396dbd5992bbe3f5cdb947e8bbc0ee413d7c17e2beaae69f5d569cf982eb",
            ),
            ("ollama_runtime_version", "0.30.6"),
        ]
        .into_iter()
        .map(|(key, value)| (key.to_string(), Value::String(value.to_string())))
        .collect();
        let mut files = vec![
            PackManifestFile {
                path: "MODEL-PROVENANCE.json".to_string(),
                bytes: 1,
                sha256: "aa".repeat(32),
            },
            PackManifestFile {
                path: "manifests/registry.ollama.ai/library/gemma4/e4b".to_string(),
                bytes: 709,
                sha256: "c6eb396dbd5992bbe3f5cdb947e8bbc0ee413d7c17e2beaae69f5d569cf982eb"
                    .to_string(),
            },
            PackManifestFile {
                path:
                    "blobs/sha256-f0988ff50a2458c598ff6b1b87b94d0f5c44d73061c2795391878b00b2285e11"
                        .to_string(),
                bytes: 473,
                sha256: "f0988ff50a2458c598ff6b1b87b94d0f5c44d73061c2795391878b00b2285e11"
                    .to_string(),
            },
            PackManifestFile {
                path:
                    "blobs/sha256-4c27e0f5b5adf02ac956c7322bd2ee7636fe3f45a8512c9aba5385242cb6e09a"
                        .to_string(),
                bytes: 9_608_338_848,
                sha256: "4c27e0f5b5adf02ac956c7322bd2ee7636fe3f45a8512c9aba5385242cb6e09a"
                    .to_string(),
            },
            PackManifestFile {
                path:
                    "blobs/sha256-7339fa418c9ad3e8e12e74ad0fd26a9cc4be8703f9c110728a992b193be85cb2"
                        .to_string(),
                bytes: 11_355,
                sha256: "7339fa418c9ad3e8e12e74ad0fd26a9cc4be8703f9c110728a992b193be85cb2"
                    .to_string(),
            },
            PackManifestFile {
                path:
                    "blobs/sha256-56380ca2ab89f1f68c283f4d50863c0bcab52ae3f1b9a88e4ab5617b176f71a3"
                        .to_string(),
                bytes: 42,
                sha256: "56380ca2ab89f1f68c283f4d50863c0bcab52ae3f1b9a88e4ab5617b176f71a3"
                    .to_string(),
            },
        ];
        let manifest = PackManifest {
            schema_version: 1,
            product: "civiccast-native".to_string(),
            component: "summary-gemma4-e4b".to_string(),
            product_version: "1.0.0-rc15".to_string(),
            compatible_core: "1.0.0-rc15".to_string(),
            signing_key_id: "test".to_string(),
            file_count: files.len(),
            total_bytes: files.iter().map(|file| file.bytes).sum(),
            files: files.clone(),
            metadata,
        };
        validate_ollama_model_contract(&manifest).expect("reviewed model contract");

        files[3].bytes = 10;
        let substituted = PackManifest { files, ..manifest };
        assert!(validate_ollama_model_contract(&substituted)
            .expect_err("smaller signed model must fail")
            .contains("reviewed model lock"));
    }

    #[test]
    fn reviewed_ollama_model_returns_the_gemma4_12b_acquisition_identity_task_56_targets() {
        // task #56's 7.6 GB `local_ai_model` component is this exact entry --
        // `acquisition_catalog.rs`'s direct-registry catalog reads it through
        // this same accessor, never a re-transcribed copy of these digests.
        let model = reviewed_ollama_model("gemma4-12b").expect("gemma4-12b is in the embedded lock");
        assert_eq!(model.registry, "registry.ollama.ai");
        assert_eq!(model.repository, "gemma4");
        assert_eq!(model.tag, "12b");
        assert_eq!(model.manifest_bytes, 905);
        assert_eq!(
            model.manifest_sha256,
            "4eb23ef187e2c5462566d6a1d3bbbc2f1346d0b4327cbb66d58fffbcc9b2b05c"
        );
        assert_eq!(model.config.bytes, 548);
        assert_eq!(
            model.config.sha256,
            "c805f5b265d8e695c44f4065dfc368206cd8026447604925fef8db57ee32ee23"
        );
        assert_eq!(model.layers.len(), 4);
        let total_layer_bytes: u64 = model.layers.iter().map(|layer| layer.bytes).sum();
        assert_eq!(
            total_layer_bytes,
            7_381_382_048 + 175_115_584 + 10_174 + 42,
            "layer bytes must sum to the pinned ~7.6 GB total unmodified"
        );
    }

    #[test]
    fn reviewed_ollama_model_rejects_an_unknown_model_name() {
        assert!(reviewed_ollama_model("not-a-real-model")
            .expect_err("unknown model name must fail loud, not silently default")
            .contains("not present in the reviewed model lock"));
    }

    // ---- per-tier caption pack verification (WP1 adaptive-tier) ----
    //
    // Mirrors `tests/native/test_caption_pack_tier_verification.py` on the
    // Python side. `test_registry_with_bound_floor` builds a registry with a
    // SYNTHETIC bound floor tier (distinct model root + distinct file
    // inventory from large-v3) purely as a function return value -- no
    // global mutation/monkeypatching needed, since `verify_caption_pack_tiers`
    // takes its registry as a parameter (the pure-classifier seam).

    const TEST_FLOOR_MODEL_ROOT: &str = "models/faster-whisper-medium";
    const TEST_FLOOR_FILES: [(&str, u64, &str); 2] = [
        (
            "config.json",
            11,
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        ),
        (
            "vocabulary.json",
            1_068_113,
            "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        ),
    ];

    fn test_registry_with_bound_floor() -> BTreeMap<&'static str, CaptionTierSpec> {
        let mut registry = caption_tier_registry();
        registry.insert(
            FLOOR_TIER_ID,
            CaptionTierSpec {
                model_root: TEST_FLOOR_MODEL_ROOT,
                files: &TEST_FLOOR_FILES,
                pending: false,
            },
        );
        registry
    }

    fn large_v3_file_entries() -> Vec<PackManifestFile> {
        CAPTION_MODEL_FILES
            .iter()
            .map(|(name, bytes, sha256)| PackManifestFile {
                path: format!("{CAPTION_MODEL_ROOT}/{name}"),
                bytes: *bytes,
                sha256: (*sha256).to_string(),
            })
            .collect()
    }

    fn floor_file_entries() -> Vec<PackManifestFile> {
        TEST_FLOOR_FILES
            .iter()
            .map(|(name, bytes, sha256)| PackManifestFile {
                path: format!("{TEST_FLOOR_MODEL_ROOT}/{name}"),
                bytes: *bytes,
                sha256: (*sha256).to_string(),
            })
            .collect()
    }

    fn valid_caption_manifest() -> PackManifest {
        let mut files = large_v3_file_entries();
        files.push(PackManifestFile {
            path: CAPTION_SELF_TEST_PATH.to_string(),
            bytes: CAPTION_SELF_TEST_BYTES,
            sha256: CAPTION_SELF_TEST_SHA256.to_string(),
        });
        let model_files: serde_json::Map<String, Value> = CAPTION_MODEL_FILES
            .iter()
            .map(|(name, bytes, sha256)| {
                (
                    (*name).to_string(),
                    json!({"bytes": bytes, "sha256": sha256}),
                )
            })
            .collect();
        let metadata: BTreeMap<String, Value> = serde_json::from_value(json!({
            "component": CAPTION_COMPONENT,
            "required": true,
            "model_architecture": "large-v3",
            "model_directory": "faster-whisper-large-v3",
            "model_repository": "Systran/faster-whisper-large-v3",
            "model_revision": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
            "model_files": Value::Object(model_files),
            "runtime_backend": "faster-whisper",
            "runtime_version": "1.2.1",
            "ctranslate2_version": "4.8.1",
            "runtime_device": "cpu",
            "runtime_compute_type": "int8",
            "hardware_acceleration_required": false,
            "self_test_audio_file": "jfk.wav",
            "self_test_audio_bytes": CAPTION_SELF_TEST_BYTES,
            "self_test_audio_sha256": CAPTION_SELF_TEST_SHA256,
            "self_test_expected_phrase": "and so my fellow americans",
            "caption_tiers": [LARGE_V3_TIER_ID]
        }))
        .expect("caption metadata");
        let total_bytes = files.iter().map(|item| item.bytes).sum();
        let file_count = files.len();
        PackManifest {
            schema_version: 1,
            product: PACK_PRODUCT.to_string(),
            component: CAPTION_COMPONENT.to_string(),
            product_version: "1.0.0-rc15".to_string(),
            compatible_core: "1.0.0-rc15".to_string(),
            signing_key_id: "test".to_string(),
            file_count,
            total_bytes,
            files,
            metadata,
        }
    }

    fn valid_whistle_manifest() -> PackManifest {
        let files = vec![
            PackManifestFile {
                path: "libneedle.dll".to_string(),
                bytes: 1_502_720,
                sha256: WHISTLE_ENGINE_DLL_SHA256.to_string(),
            },
            PackManifestFile {
                path: "whistle.cact".to_string(),
                bytes: 16_919_407,
                sha256: WHISTLE_MODEL_SHA256.to_string(),
            },
        ];
        let metadata: BTreeMap<String, Value> = serde_json::from_value(json!({
            "component": WHISTLE_COMPONENT,
            "required": true,
            "model_name": "whistle",
            "model_source_url": "https://huggingface.co/Cactus-Compute/whistle/resolve/main/whistle.cact",
            "model_bytes": 16_919_407_u64,
            "model_sha256": WHISTLE_MODEL_SHA256,
            "model_license": "Apache-2.0",
            "engine_distribution": "cactus-needle",
            "engine_version": "3.1.0",
            "engine_wheel_source_url": "https://huggingface.co/Cactus-Compute/needle3/resolve/main/python/cactus_needle-3.1.0-py3-none-win_amd64.whl",
            "engine_wheel_sha256": WHISTLE_ENGINE_WHEEL_SHA256,
            "engine_library": "libneedle.dll",
            "engine_library_bytes": 1_502_720_u64,
            "engine_library_sha256": WHISTLE_ENGINE_DLL_SHA256,
            "engine_license": "Apache-2.0"
        }))
        .expect("Whistle metadata");
        PackManifest {
            schema_version: 1,
            product: PACK_PRODUCT.to_string(),
            component: WHISTLE_COMPONENT.to_string(),
            product_version: "station-models-1".to_string(),
            compatible_core: "station-models-1".to_string(),
            signing_key_id: "test".to_string(),
            file_count: files.len(),
            total_bytes: files.iter().map(|item| item.bytes).sum(),
            files,
            metadata,
        }
    }

    #[test]
    fn whistle_contract_accepts_only_the_pinned_model_and_engine_files() {
        let manifest = valid_whistle_manifest();
        validate_whistle_pack_contract(&manifest).expect("pinned Whistle contract");

        let mut substituted_engine = valid_whistle_manifest();
        substituted_engine.files[0].sha256 = "00".repeat(32);
        assert!(validate_whistle_pack_contract(&substituted_engine)
            .expect_err("substituted engine DLL must fail")
            .contains("captions-whistle caption pack substituted unapproved bytes for libneedle.dll"));

        let mut substituted_wheel = valid_whistle_manifest();
        substituted_wheel.metadata.insert(
            "engine_wheel_sha256".to_string(),
            Value::String("00".repeat(32)),
        );
        assert!(validate_whistle_pack_contract(&substituted_wheel)
            .expect_err("unreviewed engine wheel must fail")
            .contains("reviewed contract"));
    }

    #[test]
    fn caption_contract_accepts_a_valid_single_tier_manifest() {
        validate_component_contract(&valid_caption_manifest()).expect("valid caption manifest");
    }

    #[test]
    fn caption_contract_rejects_a_manifest_missing_the_caption_tiers_declaration() {
        let mut manifest = valid_caption_manifest();
        manifest.metadata.remove("caption_tiers");

        let error = validate_component_contract(&manifest)
            .expect_err("a caption pack without the per-tier declaration must fail");
        // Aligned verbatim with the Python side's
        // `NativePackVerificationError` message (see
        // `civiccast/installer/native_packs.py::verify_caption_pack_tiers`)
        // so operator-facing docs describe one behavior across both
        // installers.
        assert_eq!(
            error,
            "caption pack metadata is missing its per-tier inventory declaration"
        );
    }

    #[test]
    fn caption_tier_registry_accepts_a_two_tier_pack() {
        let registry = test_registry_with_bound_floor();
        let mut files = large_v3_file_entries();
        files.extend(floor_file_entries());
        let present = vec![LARGE_V3_TIER_ID.to_string(), FLOOR_TIER_ID.to_string()];

        let verified = verify_caption_pack_tiers(&files, &present, &present, &registry)
            .expect("two-tier pack verifies");
        assert_eq!(
            verified,
            BTreeSet::from([LARGE_V3_TIER_ID.to_string(), FLOOR_TIER_ID.to_string()])
        );
    }

    #[test]
    fn caption_tier_registry_refuses_cross_tier_file_borrowing() {
        // The EXACT defect the R7 tester hit: a non-large-v3 tier's manifest
        // entry pointed at large-v3-hashed bytes and the old verifier could
        // not tell, because it only ever checked large-v3's hard-coded
        // inventory. Each tier must be checked against ITS OWN recorded
        // inventory, never another's.
        let registry = test_registry_with_bound_floor();
        let mut files = large_v3_file_entries();
        let mut tampered_floor = floor_file_entries();
        let (borrowed_bytes, borrowed_sha256) = CAPTION_MODEL_FILES
            .iter()
            .find(|(name, _, _)| *name == "vocabulary.json")
            .map(|(_, bytes, sha256)| (*bytes, (*sha256).to_string()))
            .expect("large-v3 vocabulary.json entry");
        tampered_floor[1].bytes = borrowed_bytes;
        tampered_floor[1].sha256 = borrowed_sha256;
        files.extend(tampered_floor);
        let present = vec![LARGE_V3_TIER_ID.to_string(), FLOOR_TIER_ID.to_string()];

        let error = verify_caption_pack_tiers(&files, &present, &present, &registry)
            .expect_err("cross-tier borrowed bytes must fail");
        assert!(error.contains("substituted"), "unexpected error: {error}");
    }

    #[test]
    fn caption_tier_registry_refuses_a_pack_missing_the_required_floor_tier() {
        let registry = test_registry_with_bound_floor();
        let files = large_v3_file_entries();
        let present = vec![LARGE_V3_TIER_ID.to_string()];
        let required = vec![LARGE_V3_TIER_ID.to_string(), FLOOR_TIER_ID.to_string()];

        let error = verify_caption_pack_tiers(&files, &present, &required, &registry)
            .expect_err("missing required floor tier must fail");
        assert!(
            error.contains("missing required tier"),
            "unexpected error: {error}"
        );
        assert!(error.contains(FLOOR_TIER_ID), "unexpected error: {error}");
    }

    #[test]
    fn caption_tier_registry_refuses_extra_files_under_a_tiers_model_directory() {
        let registry = test_registry_with_bound_floor();
        let mut files = large_v3_file_entries();
        let mut floor_files = floor_file_entries();
        floor_files.push(PackManifestFile {
            path: format!("{TEST_FLOOR_MODEL_ROOT}/unexpected-extra-file.bin"),
            bytes: 4,
            sha256: "e".repeat(64),
        });
        files.extend(floor_files);
        let present = vec![LARGE_V3_TIER_ID.to_string(), FLOOR_TIER_ID.to_string()];

        let error = verify_caption_pack_tiers(&files, &present, &present, &registry)
            .expect_err("extra undeclared file must fail");
        assert!(
            error.contains("unexpected files"),
            "unexpected error: {error}"
        );
    }

    #[test]
    fn caption_tier_registry_refuses_an_inventory_claiming_files_the_pack_lacks() {
        let registry = test_registry_with_bound_floor();
        let mut files = large_v3_file_entries();
        let floor_files = floor_file_entries();
        files.push(floor_files[0].clone());
        let present = vec![LARGE_V3_TIER_ID.to_string(), FLOOR_TIER_ID.to_string()];

        let error = verify_caption_pack_tiers(&files, &present, &present, &registry)
            .expect_err("missing declared file must fail");
        assert!(
            error.contains("missing declared files"),
            "unexpected error: {error}"
        );
    }

    #[test]
    fn caption_tier_registry_refuses_a_pending_unbound_tier() {
        // Historical note: this test used to exercise the REAL registry's
        // floor placeholder (WP1's initial per-tier-inventory fix landed the
        // floor tier pending, before the owner named a model). The owner's
        // 2026-07-30 BINDING ruling (`medium`) closed that placeholder in the
        // real registry -- see
        // `caption_tier_registry_floor_entry_is_bound_to_the_owner_ruled_medium_model`
        // below -- so this test now proves the invariant it always meant to
        // prove (an unbound tier must never verify as present, whatever its
        // id) against a LOCAL synthetic pending spec instead of the real one.
        let mut registry = caption_tier_registry();
        registry.insert(
            FLOOR_TIER_ID,
            CaptionTierSpec {
                model_root: "models/some-future-tier-pending-owner-binding",
                files: &CAPTION_NO_TIER_FILES,
                pending: true,
            },
        );
        let files = large_v3_file_entries();
        let present = vec![LARGE_V3_TIER_ID.to_string(), FLOOR_TIER_ID.to_string()];
        let required = vec![LARGE_V3_TIER_ID.to_string()];

        let error = verify_caption_pack_tiers(&files, &present, &required, &registry)
            .expect_err("unbound pending tier must fail");
        assert!(
            error.contains("not yet bound") || error.contains("pending"),
            "unexpected error: {error}"
        );
    }

    #[test]
    fn caption_tier_registry_floor_entry_is_bound_to_the_owner_ruled_medium_model() {
        // The owner's BINDING ruling (OWNER-DECISION-caption-adaptive-tier.md,
        // 2026-07-30) named `medium` as the floor tier. The REAL registry's
        // floor entry must reflect that: not pending, a non-empty pinned
        // inventory, and an identity that names the medium model -- never
        // large-v3's model root or files.
        let registry = caption_tier_registry();
        let floor = registry.get(FLOOR_TIER_ID).expect("floor tier entry");

        assert!(!floor.pending, "the real floor tier must no longer be pending");
        assert_eq!(floor.model_root, CAPTION_FLOOR_TIER_MODEL_ROOT);
        assert_eq!(floor.model_root, "models/faster-whisper-medium");
        assert_ne!(floor.model_root, CAPTION_MODEL_ROOT);
        assert!(!floor.files.is_empty());
        assert_eq!(floor.files, &CAPTION_FLOOR_TIER_MODEL_FILES);
        assert_ne!(floor.files, &CAPTION_MODEL_FILES[..]);
        assert_eq!(CAPTION_FLOOR_TIER_MODEL_REPOSITORY, "Systran/faster-whisper-medium");
        assert_eq!(
            CAPTION_FLOOR_TIER_MODEL_REVISION,
            "08e178d48790749d25932bbc082711ddcfdfbc4f"
        );
    }

    #[test]
    fn caption_tier_registry_accepts_the_owner_bound_real_floor_tier() {
        // End-to-end version of the synthetic two-tier test above, but
        // against the REAL registry's now-bound floor entry (not a test
        // double), proving a pack carrying the real medium tier verifies.
        let registry = caption_tier_registry();
        let mut files = large_v3_file_entries();
        files.extend(CAPTION_FLOOR_TIER_MODEL_FILES.iter().map(|(name, bytes, sha256)| {
            PackManifestFile {
                path: format!("{CAPTION_FLOOR_TIER_MODEL_ROOT}/{name}"),
                bytes: *bytes,
                sha256: (*sha256).to_string(),
            }
        }));
        let present = vec![LARGE_V3_TIER_ID.to_string(), FLOOR_TIER_ID.to_string()];

        let verified = verify_caption_pack_tiers(&files, &present, &present, &registry)
            .expect("real bound floor tier verifies");
        assert_eq!(
            verified,
            BTreeSet::from([LARGE_V3_TIER_ID.to_string(), FLOOR_TIER_ID.to_string()])
        );
    }

    #[test]
    fn caption_tier_registry_refuses_an_unknown_declared_tier() {
        let registry = caption_tier_registry();
        let files = large_v3_file_entries();
        let present = vec![LARGE_V3_TIER_ID.to_string(), "turbo-xl".to_string()];
        let required = vec![LARGE_V3_TIER_ID.to_string()];

        let error = verify_caption_pack_tiers(&files, &present, &required, &registry)
            .expect_err("unknown declared tier must fail");
        assert!(error.contains("unknown tier"), "unexpected error: {error}");
    }

    // -----------------------------------------------------------------
    // <installer-path-audit BL-08> The mandatory caption FLOOR model's
    // content contract.
    //
    // Before this, `validate_component_contract` had three branches --
    // SOURCE_BOUND_COMPONENTS, `captions-large-v3`, and the three Ollama ids
    // -- and a grep for "captions-floor" in this file found NONE. The
    // owner-designated MANDATORY caption baseline hit no branch at all, so
    // the complete set of machine checks on it was: trust-root signature,
    // component id, outer SHA-256 == the index's, and byte count == the
    // index's. Every one of those is a value the publisher chose and signed
    // in ONE operation. A release engineer pointing `--captions-floor-root`
    // at `faster-whisper-small` shipped an unreviewed ASR model to every
    // station on the legally non-negotiable captions path, with every gate
    // green -- and the residual live self-test only checks that `jfk.wav`
    // transcribes to "and so my fellow americans", which `small`, `base` and
    // most quantizations also do.
    // -----------------------------------------------------------------

    fn floor_manifest(signing_key_id: &str) -> PackManifest {
        let mut files: Vec<PackManifestFile> = CAPTION_FLOOR_TIER_MODEL_FILES
            .iter()
            .map(|(name, bytes, sha256)| PackManifestFile {
                path: format!("{CAPTION_FLOOR_TIER_MODEL_ROOT}/{name}"),
                bytes: *bytes,
                sha256: (*sha256).to_string(),
            })
            .collect();
        files.push(PackManifestFile {
            path: CAPTION_SELF_TEST_PATH.to_string(),
            bytes: CAPTION_SELF_TEST_BYTES,
            sha256: CAPTION_SELF_TEST_SHA256.to_string(),
        });
        PackManifest {
            schema_version: 1,
            product: "civiccast-native".to_string(),
            component: "captions-floor".to_string(),
            product_version: "1.0.0-beta.3".to_string(),
            compatible_core: "1.0.0-beta.3".to_string(),
            signing_key_id: signing_key_id.to_string(),
            file_count: files.len(),
            total_bytes: files.iter().map(|item| item.bytes).sum(),
            files,
            metadata: std::collections::BTreeMap::new(),
        }
    }

    #[test]
    fn a_release_signed_floor_pack_carrying_the_reviewed_model_verifies() {
        validate_component_contract(&floor_manifest("civiccast-release-2026"))
            .expect("the owner-ruled medium floor model must verify");
    }

    #[test]
    fn a_release_signed_floor_pack_with_substituted_model_bytes_is_refused() {
        // The audit's own proof: a floor pack built from a DIFFERENT
        // faster-whisper model (small, or a re-quantized medium) verified
        // before this branch existed.
        let mut manifest = floor_manifest("civiccast-release-2026");
        let model = manifest
            .files
            .iter_mut()
            .find(|item| item.path.ends_with("model.bin"))
            .expect("the floor inventory must name model.bin");
        model.bytes = 483_546_902; // faster-whisper-small's model.bin
        model.sha256 = "00".repeat(32);

        let error = validate_component_contract(&manifest)
            .expect_err("a substituted floor model must be refused");
        assert!(error.contains("model.bin"), "unexpected error: {error}");
    }

    #[test]
    fn a_release_signed_floor_pack_missing_a_model_file_is_refused() {
        let mut manifest = floor_manifest("civiccast-release-2026");
        manifest.files.retain(|item| !item.path.ends_with("tokenizer.json"));
        let error = validate_component_contract(&manifest)
            .expect_err("an incomplete floor inventory must be refused");
        assert!(error.contains("tokenizer.json"), "unexpected error: {error}");
    }

    #[test]
    fn a_release_signed_floor_pack_missing_the_self_test_fixture_is_refused() {
        let mut manifest = floor_manifest("civiccast-release-2026");
        manifest
            .files
            .retain(|item| item.path != CAPTION_SELF_TEST_PATH);
        let error = validate_component_contract(&manifest)
            .expect_err("a floor pack without the self-test fixture must be refused");
        assert!(error.contains(CAPTION_SELF_TEST_PATH), "unexpected error: {error}");
        assert!(
            error.contains("captions-floor"),
            "the refusal must name the FLOOR pack, not large-v3: {error}"
        );
    }

    #[test]
    fn a_floor_pack_borrowing_large_v3s_model_bytes_is_refused() {
        // The cross-tier substitution `verify_caption_pack_tiers` exists to
        // prevent -- previously unreachable for this component because the
        // component reached no branch.
        let mut manifest = floor_manifest("civiccast-release-2026");
        let large = &CAPTION_MODEL_FILES
            .iter()
            .find(|(name, _, _)| *name == "model.bin")
            .expect("large-v3 inventory names model.bin");
        let model = manifest
            .files
            .iter_mut()
            .find(|item| item.path.ends_with("model.bin"))
            .expect("the floor inventory must name model.bin");
        model.bytes = large.1;
        model.sha256 = large.2.to_string();

        validate_component_contract(&manifest)
            .expect_err("a floor pack carrying large-v3's model bytes must be refused");
    }

    #[test]
    fn a_development_signed_floor_pack_is_exempt_and_that_boundary_is_deliberate() {
        // The exemption exists so the whole-bundle round-trip fixtures (which
        // cannot synthesise a 1.5 GB model.bin at its pinned SHA-256) keep
        // working. It cannot widen any shipping path: `embedded_pack_trust`
        // refuses a bootstrap whose embedded key id starts with
        // `development-` unless it was compiled with
        // CIVICCAST_ALLOW_DEVELOPMENT_PACK_KEY=1, and a pack's signature must
        // verify against THAT bootstrap's key.
        let mut manifest = floor_manifest("development-test-key");
        manifest.files.clear();
        manifest.file_count = 0;
        manifest.total_bytes = 0;
        validate_component_contract(&manifest)
            .expect("a development-signed floor pack is exempt from the pinned inventory");

        // ...and the same manifest under a release key is refused, which is
        // what makes the boundary a boundary rather than a hole.
        let mut release = floor_manifest("civiccast-release-2026");
        release.files.clear();
        release.file_count = 0;
        release.total_bytes = 0;
        validate_component_contract(&release)
            .expect_err("the same empty pack under a release key must be refused");
    }
}
