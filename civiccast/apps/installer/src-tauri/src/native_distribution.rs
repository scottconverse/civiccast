// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors

use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine as _;
use ed25519_dalek::{Signature, Verifier};
use reqwest::blocking::{Client, Response};
use reqwest::header::{ACCEPT_ENCODING, CONTENT_LENGTH, CONTENT_RANGE, RANGE};
use reqwest::redirect::Policy;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use url::Url;

use crate::native_packs::PackTrust;
use crate::native_packs::{self, VerifiedPack};

const DISTRIBUTION_SCHEMA_VERSION: u32 = 1;
const DISTRIBUTION_PRODUCT: &str = "civiccast-native";
// Owner decision (Scott Converse, 2026-08-07, ratified): the caption FLOOR
// tier (`medium` / `captions-floor`) is the mandatory baseline for station
// activation; `captions-large-v3` is an optional quality add-on, verified
// when present and simply absent when not. `captions-floor` is the SAME
// component id the GUI acquisition flow already uses on disk
// (`acquisition_catalog.rs`'s `captions-floor` staging directory,
// `station_runtime.py`'s `FLOOR_TIER_ID` model-root prefix) -- never a
// second, invented convention for the same tier.
const REQUIRED_COMPONENTS: [&str; 6] = [
    "core",
    "captions-floor",
    "summary-gemma4-12b",
    "summary-gemma4-e4b",
    "translation-translategemma-4b",
    "captions-whistle",
];
// `captions-large-v3` is intentionally NOT in `REQUIRED_COMPONENTS`: it is
// an optional quality add-on (owner decision 2026-08-07), verified when a
// distribution carries it and simply absent when it does not. See
// `native_activation.rs::OPTIONAL_COMPONENTS` for the staging/manifest side
// of this same decision.

#[derive(Debug, Clone, Serialize)]
pub struct VerifiedDistribution {
    pub sha256: String,
    pub kind: String,
    pub channel: String,
    pub product_version: String,
    pub compatible_core: String,
    pub signing_key_id: String,
    pub created_epoch: u64,
    pub packs: Vec<DistributionPack>,
}

#[derive(Debug, Clone, Serialize)]
pub struct DistributionPack {
    pub component: String,
    pub filename: String,
    pub bytes: u64,
    pub sha256: String,
    pub required: bool,
    pub urls: Vec<String>,
}

pub struct TransferResponse {
    pub status: u16,
    pub content_range: Option<String>,
    pub content_length: Option<u64>,
    pub body: Box<dyn Read + Send>,
}

#[derive(Debug, Clone, Serialize)]
pub struct AcquiredDistribution {
    pub index: VerifiedDistribution,
    pub packs: Vec<AcquiredPack>,
}

#[derive(Debug, Clone, Serialize)]
pub struct AcquiredPack {
    pub component: String,
    pub cached_path: PathBuf,
    pub outer_sha256: String,
    pub verified: VerifiedPack,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct DistributionEnvelope {
    manifest: Value,
    signature: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct DistributionManifest {
    schema_version: u32,
    product: String,
    kind: String,
    channel: String,
    product_version: String,
    compatible_core: String,
    signing_key_id: String,
    created_epoch: u64,
    packs: Vec<DistributionManifestPack>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct DistributionManifestPack {
    component: String,
    filename: String,
    bytes: u64,
    sha256: String,
    required: bool,
    urls: Vec<String>,
}

pub fn verify_distribution_bytes(
    raw: &[u8],
    trust: &PackTrust,
    expected_kind: Option<&str>,
    expected_channel: Option<&str>,
    expected_product_version: Option<&str>,
    expected_compatible_core: Option<&str>,
) -> Result<VerifiedDistribution, String> {
    if raw.len() > 4 * 1024 * 1024 {
        return Err("Native distribution index is too large.".to_string());
    }
    let envelope_value: Value = serde_json::from_slice(raw)
        .map_err(|error| format!("Native distribution index is invalid JSON: {error}"))?;
    if canonical_json(&envelope_value)?.as_bytes() != raw {
        return Err("Native distribution index is not canonical JSON.".to_string());
    }
    let envelope: DistributionEnvelope = serde_json::from_value(envelope_value)
        .map_err(|error| format!("Native distribution envelope is malformed: {error}"))?;
    if BASE64.encode(
        BASE64
            .decode(&envelope.signature)
            .map_err(|error| format!("Native distribution signature is invalid base64: {error}"))?,
    ) != envelope.signature
    {
        return Err("Native distribution signature is not canonical base64.".to_string());
    }
    let signature_bytes = BASE64
        .decode(&envelope.signature)
        .map_err(|error| format!("Native distribution signature is invalid base64: {error}"))?;
    let signature = Signature::from_slice(&signature_bytes)
        .map_err(|error| format!("Native distribution signature length is invalid: {error}"))?;
    let manifest_bytes = canonical_json(&envelope.manifest)?;
    trust
        .public_key
        .verify(manifest_bytes.as_bytes(), &signature)
        .map_err(|_| "Native distribution index signature is invalid.".to_string())?;
    let manifest: DistributionManifest = serde_json::from_value(envelope.manifest)
        .map_err(|error| format!("Native distribution manifest is malformed: {error}"))?;

    if manifest.schema_version != DISTRIBUTION_SCHEMA_VERSION
        || manifest.product != DISTRIBUTION_PRODUCT
    {
        return Err("Native distribution schema or product identity is invalid.".to_string());
    }
    if !matches!(manifest.kind.as_str(), "channel-index" | "station-index") {
        return Err("Native distribution kind is invalid.".to_string());
    }
    for (field, value) in [
        ("kind", manifest.kind.as_str()),
        ("channel", manifest.channel.as_str()),
        ("product version", manifest.product_version.as_str()),
        ("compatible core", manifest.compatible_core.as_str()),
        ("signing key id", manifest.signing_key_id.as_str()),
    ] {
        require_identity(field, value)?;
    }
    if manifest.signing_key_id != trust.key_id {
        return Err(
            "Native distribution signing key id does not match the embedded trust root."
                .to_string(),
        );
    }
    for (label, observed, expected) in [
        ("kind", manifest.kind.as_str(), expected_kind),
        ("channel", manifest.channel.as_str(), expected_channel),
        (
            "product version",
            manifest.product_version.as_str(),
            expected_product_version,
        ),
        (
            "compatible core",
            manifest.compatible_core.as_str(),
            expected_compatible_core,
        ),
    ] {
        if expected.is_some_and(|value| value != observed) {
            return Err(format!(
                "Native distribution {label} does not match the bootstrap."
            ));
        }
    }
    if manifest.packs.is_empty() {
        return Err("Native distribution has no component packs.".to_string());
    }

    let mut seen_components = BTreeSet::new();
    let mut seen_filenames = BTreeSet::new();
    let mut packs = Vec::with_capacity(manifest.packs.len());
    for item in manifest.packs {
        require_component(&item.component)?;
        if !seen_components.insert(item.component.clone()) {
            return Err(format!(
                "Native distribution contains duplicate component: {}",
                item.component
            ));
        }
        let filename = safe_pack_filename(&item.filename)?;
        if !seen_filenames.insert(filename.to_lowercase()) {
            return Err(format!(
                "Native distribution contains duplicate pack filename: {filename}"
            ));
        }
        if item.bytes == 0 {
            return Err(format!(
                "Native distribution pack byte count is invalid: {}",
                item.component
            ));
        }
        if !is_lower_hex_sha256(&item.sha256) {
            return Err(format!(
                "Native distribution pack SHA-256 is invalid: {}",
                item.component
            ));
        }
        if REQUIRED_COMPONENTS.contains(&item.component.as_str()) && !item.required {
            return Err(format!(
                "Native distribution component must be required: {}",
                item.component
            ));
        }
        validate_urls(&manifest.kind, &item.component, &item.urls)?;
        packs.push(DistributionPack {
            component: item.component,
            filename,
            bytes: item.bytes,
            sha256: item.sha256,
            required: item.required,
            urls: item.urls,
        });
    }
    let missing: Vec<_> = REQUIRED_COMPONENTS
        .iter()
        .filter(|component| !seen_components.contains(**component))
        .copied()
        .collect();
    if !missing.is_empty() {
        return Err(format!(
            "Native distribution required component set is incomplete: {}",
            missing.join(", ")
        ));
    }
    let mut expected_order: Vec<_> = seen_components.iter().cloned().collect();
    expected_order.sort_by_key(|component| component_sort_key(component));
    let observed_order: Vec<_> = packs.iter().map(|pack| pack.component.clone()).collect();
    if observed_order != expected_order {
        return Err("Native distribution components are not in canonical order.".to_string());
    }

    Ok(VerifiedDistribution {
        sha256: format!("{:x}", Sha256::digest(raw)),
        kind: manifest.kind,
        channel: manifest.channel,
        product_version: manifest.product_version,
        compatible_core: manifest.compatible_core,
        signing_key_id: manifest.signing_key_id,
        created_epoch: manifest.created_epoch,
        packs,
    })
}

/// The station MODEL components -- the ones whose packs carry
/// `scripts/build_native_station_bundle.py`'s stable
/// `STATION_MODEL_PACK_PRODUCT_VERSION` identity rather than the product
/// version. Derived from the existing component constants, never retyped:
/// [`REQUIRED_COMPONENTS`] minus its leading `core` (the per-version
/// placeholder), plus `native_activation::OPTIONAL_COMPONENTS`
/// (`captions-large-v3`).
///
/// This is an ALLOWLIST on purpose, not "everything except `core`". A
/// future non-model component added to a station bundle -- a config pack, a
/// license pack, anything genuinely per-version -- must keep the exact
/// version contract by DEFAULT, and only be added here deliberately, by
/// someone who has also made its bytes reproducible across candidates.
/// `station_model_components_are_derived_from_the_component_constants`
/// pins the `core`-is-first assumption this slice relies on.
fn is_station_model_component(component: &str) -> bool {
    REQUIRED_COMPONENTS[1..].contains(&component)
        || crate::native_activation::OPTIONAL_COMPONENTS.contains(&component)
}

/// What a component pack's OWN signed manifest must declare for
/// `product_version` / `compatible_core`, given the signed index that
/// references it. `None` means "do not compare that field" -- never "do not
/// verify this pack": the pack's signature, component id, outer SHA-256 and
/// outer byte count are checked in every case.
///
/// A **station index** (the air-gapped `$EXEDIR\station` side-load) pins
/// each MODEL pack ([`is_station_model_component`]) by the SHA-256 and byte
/// count in the index the trust root signed. Those packs are built with a
/// deliberately stable identity
/// (`scripts/build_native_station_bundle.py`'s
/// `STATION_MODEL_PACK_PRODUCT_VERSION` / `STATION_MODEL_PACK_COMPATIBLE_CORE`)
/// and with reproducible bytes, so the same reviewed model set hashes
/// identically from one product candidate to the next. That is what lets an
/// already-activated station reuse the ~21 GB of model packs already
/// sitting in its per-SHA cache
/// (`<install root>\packs\.station-cache\packs\<sha256>.ccpack`) on a
/// download-only upgrade whose `setup.exe` ships with no `station\` folder
/// beside it -- see [`copy_station_pack_to_cache`]'s cache fallback.
///
/// **What this gives up, stated plainly.** The declared version pair was a
/// second, independent tripwire: a publisher who signed a NEW index that
/// referenced a pack built in a STALE era would previously have been caught
/// by the version mismatch. That tripwire is gone for model packs. What
/// remains is the SHA-256 the publisher themselves chose and signed into
/// the index -- so a publisher holding the trust-root key who points a new
/// index at an old pack's digest gets exactly that old pack, silently and
/// by construction. The residual protection is that the digest is signed
/// (nobody outside the key holder can substitute bytes) and that the
/// reviewed model lock still gates the three Ollama components' contents;
/// what is NOT protected is the key holder's own mistake about WHICH
/// reviewed era a digest belongs to. Bumping
/// `STATION_MODEL_PACK_PRODUCT_VERSION` when the reviewed model set changes
/// is what keeps that mistake visible, and it is a human discipline, not a
/// machine check.
///
/// `core` keeps the strict per-version check on both kinds of index, so
/// does any component not on the model allowlist, and a **channel index**
/// keeps it for every component: an online acquisition has no reuse story
/// to serve and the index's version pair is the bootstrap's own
/// expectation.
pub fn pack_identity_expectations<'a>(
    index: &'a VerifiedDistribution,
    component: &str,
) -> (Option<&'a str>, Option<&'a str>) {
    if index.kind == "station-index" && is_station_model_component(component) {
        return (None, None);
    }
    (
        Some(index.product_version.as_str()),
        Some(index.compatible_core.as_str()),
    )
}

pub fn download_pack_with<F>(
    pack: &DistributionPack,
    cache_root: &Path,
    mut transfer: F,
) -> Result<PathBuf, String>
where
    F: FnMut(&str, u64) -> Result<TransferResponse, String>,
{
    fs::create_dir_all(cache_root)
        .map_err(|error| format!("Could not create native pack cache: {error}"))?;
    let final_path = cache_root.join(format!("{}.ccpack", pack.sha256));
    let partial_path = cache_root.join(format!("{}.partial", pack.sha256));

    if final_path.exists() {
        if file_matches(&final_path, pack.bytes, &pack.sha256)? {
            return Ok(final_path);
        }
        fs::remove_file(&final_path)
            .map_err(|error| format!("Could not remove corrupt cached pack: {error}"))?;
    }
    if partial_path.exists() {
        let size = regular_file_size(&partial_path, "partial native pack")?;
        if size > pack.bytes {
            fs::remove_file(&partial_path)
                .map_err(|error| format!("Could not remove oversized partial pack: {error}"))?;
        } else if size == pack.bytes {
            if file_matches(&partial_path, pack.bytes, &pack.sha256)? {
                fs::rename(&partial_path, &final_path)
                    .map_err(|error| format!("Could not promote verified cached pack: {error}"))?;
                return Ok(final_path);
            }
            fs::remove_file(&partial_path)
                .map_err(|error| format!("Could not remove corrupt partial pack: {error}"))?;
        }
    }

    let mut last_error = "Native component pack has no download locations.".to_string();
    for location in &pack.urls {
        let offset = if partial_path.exists() {
            regular_file_size(&partial_path, "partial native pack")?
        } else {
            0
        };
        let response = match transfer(location, offset) {
            Ok(response) => response,
            Err(error) => {
                last_error = error;
                continue;
            }
        };
        match apply_transfer_response(pack, &partial_path, offset, response) {
            Ok(()) => {
                if !file_matches(&partial_path, pack.bytes, &pack.sha256)? {
                    let _ = fs::remove_file(&partial_path);
                    return Err(format!(
                        "Downloaded native component pack failed SHA-256 verification: {}",
                        pack.component
                    ));
                }
                fs::rename(&partial_path, &final_path)
                    .map_err(|error| format!("Could not promote verified cached pack: {error}"))?;
                return Ok(final_path);
            }
            Err(error) => last_error = error,
        }
    }
    Err(last_error)
}

pub fn acquire_online_distribution(
    index_url: &str,
    cache_root: &Path,
    trust: &PackTrust,
    expected_channel: &str,
    expected_product_version: &str,
    expected_compatible_core: &str,
) -> Result<AcquiredDistribution, String> {
    validate_https_location(index_url, "channel index")?;
    let client = build_http_client()?;
    let response = client
        .get(index_url)
        .header(ACCEPT_ENCODING, "identity")
        .send()
        .map_err(|error| format!("Could not download native channel index: {error}"))?;
    if response.status().as_u16() != 200 {
        return Err(format!(
            "Native channel index server returned HTTP status {}.",
            response.status().as_u16()
        ));
    }
    if response
        .content_length()
        .is_some_and(|length| length > 4 * 1024 * 1024)
    {
        return Err("Native channel index exceeds the maximum signed size.".to_string());
    }
    let index_bytes = read_bounded_response(response, 4 * 1024 * 1024)?;
    let index = verify_distribution_bytes(
        &index_bytes,
        trust,
        Some("channel-index"),
        Some(expected_channel),
        Some(expected_product_version),
        Some(expected_compatible_core),
    )?;
    retain_verified_index(cache_root, &index, &index_bytes, "ccindex")?;
    acquire_verified_distribution(index, cache_root, trust, |pack| {
        download_pack_with(pack, &cache_root.join("packs"), |location, offset| {
            send_pack_request(&client, location, offset)
        })
    })
}

pub fn acquire_station_distribution(
    station_index: &Path,
    cache_root: &Path,
    trust: &PackTrust,
    expected_channel: &str,
    expected_product_version: &str,
    expected_compatible_core: &str,
) -> Result<AcquiredDistribution, String> {
    let index_bytes =
        read_regular_file_bounded(station_index, "native station index", 4 * 1024 * 1024)?;
    let index = verify_distribution_bytes(
        &index_bytes,
        trust,
        Some("station-index"),
        Some(expected_channel),
        Some(expected_product_version),
        Some(expected_compatible_core),
    )?;
    retain_verified_index(cache_root, &index, &index_bytes, "ccstation")?;
    let media_root = station_index
        .canonicalize()
        .map_err(|error| format!("Could not resolve native station index: {error}"))?
        .parent()
        .ok_or_else(|| "Native station index has no media directory.".to_string())?
        .to_path_buf();
    acquire_verified_distribution(index, cache_root, trust, |pack| {
        copy_station_pack_to_cache(pack, &media_root, &cache_root.join("packs"), trust)
    })
}

fn acquire_verified_distribution<F>(
    index: VerifiedDistribution,
    cache_root: &Path,
    trust: &PackTrust,
    mut obtain: F,
) -> Result<AcquiredDistribution, String>
where
    F: FnMut(&DistributionPack) -> Result<PathBuf, String>,
{
    fs::create_dir_all(cache_root)
        .map_err(|error| format!("Could not create native distribution cache: {error}"))?;
    let mut acquired = Vec::with_capacity(index.packs.len());
    for pack in &index.packs {
        let cached_path = obtain(pack)?;
        let (expected_product_version, expected_compatible_core) =
            pack_identity_expectations(&index, &pack.component);
        let verified = native_packs::verify_pack(
            &cached_path,
            trust,
            Some(&pack.component),
            expected_product_version,
            expected_compatible_core,
        )?;
        if verified.sha256 != pack.sha256 {
            return Err(format!(
                "Native component pack outer SHA-256 does not match its signed index: {}",
                pack.component
            ));
        }
        if regular_file_size(&cached_path, "cached native pack")? != pack.bytes {
            return Err(format!(
                "Native component pack outer byte count does not match its signed index: {}",
                pack.component
            ));
        }
        acquired.push(AcquiredPack {
            component: pack.component.clone(),
            cached_path,
            outer_sha256: pack.sha256.clone(),
            verified,
        });
    }
    Ok(AcquiredDistribution {
        index,
        packs: acquired,
    })
}

fn build_http_client() -> Result<Client, String> {
    Client::builder()
        .redirect(Policy::none())
        .connect_timeout(Duration::from_secs(30))
        .timeout(Duration::from_secs(6 * 60 * 60))
        .user_agent("CivicCast-Native-Bootstrap/1")
        .build()
        .map_err(|error| format!("Could not initialize native pack HTTPS client: {error}"))
}

fn send_pack_request(
    client: &Client,
    location: &str,
    offset: u64,
) -> Result<TransferResponse, String> {
    validate_https_location(location, "component pack")?;
    let mut request = client.get(location).header(ACCEPT_ENCODING, "identity");
    if offset > 0 {
        request = request.header(RANGE, format!("bytes={offset}-"));
    }
    let response = request
        .send()
        .map_err(|error| format!("Could not download native component pack: {error}"))?;
    let status = response.status().as_u16();
    let content_range = header_text(&response, CONTENT_RANGE)?;
    let content_length = header_text(&response, CONTENT_LENGTH)?
        .map(|value| {
            value
                .parse::<u64>()
                .map_err(|_| "Native pack response Content-Length is invalid.".to_string())
        })
        .transpose()?;
    Ok(TransferResponse {
        status,
        content_range,
        content_length,
        body: Box::new(response),
    })
}

fn header_text(
    response: &Response,
    name: reqwest::header::HeaderName,
) -> Result<Option<String>, String> {
    response
        .headers()
        .get(name)
        .map(|value| {
            value
                .to_str()
                .map(str::to_string)
                .map_err(|_| "Native pack response contains a non-ASCII header.".to_string())
        })
        .transpose()
}

fn validate_https_location(location: &str, label: &str) -> Result<(), String> {
    if !location.is_ascii() {
        return Err(format!("Native {label} location is not portable ASCII."));
    }
    let parsed = Url::parse(location)
        .map_err(|_| format!("Native {label} location is not a valid HTTPS URL."))?;
    if parsed.scheme() != "https"
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.fragment().is_some()
        || parsed.path().is_empty()
        || parsed.path() == "/"
    {
        return Err(format!(
            "Native {label} location must be unambiguous HTTPS."
        ));
    }
    Ok(())
}

fn read_bounded_response(mut response: Response, maximum: usize) -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    response
        .by_ref()
        .take((maximum + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|error| format!("Could not read native channel index: {error}"))?;
    if bytes.len() > maximum {
        return Err("Native channel index exceeds the maximum signed size.".to_string());
    }
    Ok(bytes)
}

fn read_regular_file_bounded(path: &Path, label: &str, maximum: u64) -> Result<Vec<u8>, String> {
    let size = regular_file_size(path, label)?;
    if size > maximum {
        return Err(format!("{label} exceeds the maximum signed size."));
    }
    let mut input = File::open(path).map_err(|error| format!("Could not open {label}: {error}"))?;
    let mut bytes = Vec::with_capacity(size as usize);
    input
        .read_to_end(&mut bytes)
        .map_err(|error| format!("Could not read {label}: {error}"))?;
    Ok(bytes)
}

fn retain_verified_index(
    cache_root: &Path,
    index: &VerifiedDistribution,
    bytes: &[u8],
    extension: &str,
) -> Result<PathBuf, String> {
    let index_root = cache_root.join("indexes");
    fs::create_dir_all(&index_root)
        .map_err(|error| format!("Could not create native index cache: {error}"))?;
    let destination = index_root.join(format!("{}.{}", index.sha256, extension));
    if destination.exists() {
        if file_matches(&destination, bytes.len() as u64, &index.sha256)? {
            return Ok(destination);
        }
        fs::remove_file(&destination)
            .map_err(|error| format!("Could not remove corrupt cached index: {error}"))?;
    }
    let partial = destination.with_extension(format!("{extension}.partial"));
    let mut output = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&partial)
        .map_err(|error| format!("Could not create cached native index: {error}"))?;
    output
        .write_all(bytes)
        .map_err(|error| format!("Could not write cached native index: {error}"))?;
    output
        .sync_all()
        .map_err(|error| format!("Could not flush cached native index: {error}"))?;
    drop(output);
    fs::rename(&partial, &destination)
        .map_err(|error| format!("Could not promote cached native index: {error}"))?;
    Ok(destination)
}

/// The download-only-upgrade fallback for [`copy_station_pack_to_cache`]:
/// serve `pack` from this station's per-SHA cache when its bytes are not
/// beside the installer. Fails closed, naming BOTH paths, unless the cache
/// entry is byte-identical to what the signed index pins and independently
/// re-verifies as a signed pack for this component.
fn station_pack_from_cache(
    pack: &DistributionPack,
    missing_media_path: &Path,
    cache_root: &Path,
    trust: &PackTrust,
) -> Result<PathBuf, String> {
    let cached = cache_root.join(format!("{}.ccpack", pack.sha256));
    let refuse = || {
        format!(
            "Native station component pack {} is not beside the installer at {} \
             and is not already cached on this station at {}.",
            pack.component,
            missing_media_path.display(),
            cached.display()
        )
    };
    if !cached.is_file() || !file_matches(&cached, pack.bytes, &pack.sha256)? {
        return Err(refuse());
    }
    // The bytes hash to what the index pins; now prove they are a real,
    // trust-root-signed pack for THIS component (not merely a file with a
    // convenient name). Identity expectations are deliberately not asserted
    // here -- see [`pack_identity_expectations`]; `acquire_verified_distribution`
    // applies the right pair for the index kind immediately after.
    let (verified, _archive) =
        native_packs::open_and_verify_pack(&cached, trust, Some(&pack.component), None, None)
            .map_err(|_| refuse())?;
    if verified.sha256 != pack.sha256 {
        return Err(refuse());
    }
    Ok(cached)
}

fn copy_station_pack_to_cache(
    pack: &DistributionPack,
    media_root: &Path,
    cache_root: &Path,
    trust: &PackTrust,
) -> Result<PathBuf, String> {
    fs::create_dir_all(cache_root)
        .map_err(|error| format!("Could not create native pack cache: {error}"))?;
    let source = media_root.join(&pack.filename);
    if !source.exists() {
        // A download-only upgrade: `setup.exe` arrived with no `station\`
        // folder beside it, so this pack's bytes are not on the media. An
        // already-activated station still holds them in its per-SHA cache
        // (the packs are identity-stable across candidates -- see
        // [`pack_identity_expectations`]), so serve them from there. NEVER
        // on the strength of the file merely existing: the cache entry must
        // match the signed index's byte count AND SHA-256, and must itself
        // re-open and re-verify as a signed pack for this component whose
        // outer digest is the one the index pins.
        return station_pack_from_cache(pack, &source, cache_root, trust);
    }
    let canonical_source = source
        .canonicalize()
        .map_err(|error| format!("Could not resolve station component pack: {error}"))?;
    if canonical_source.parent() != Some(media_root) {
        return Err(format!(
            "Native station component pack escapes its media directory: {}",
            pack.filename
        ));
    }
    if regular_file_size(&canonical_source, "station pack")? != pack.bytes {
        return Err(format!(
            "Native station pack outer identity does not match its signed index: {}",
            pack.component
        ));
    }
    let final_path = cache_root.join(format!("{}.ccpack", pack.sha256));
    if final_path.exists() && file_matches(&final_path, pack.bytes, &pack.sha256)? {
        return Ok(final_path);
    }
    if final_path.exists() {
        fs::remove_file(&final_path)
            .map_err(|error| format!("Could not remove corrupt cached pack: {error}"))?;
    }
    // One read of the media pack: hash the bytes AS they are copied and
    // promote the copy only if they equal what the signed index pins. RC4
    // install timing (2026-11-03) found this path used to read every station
    // pack four times (signature+entry verify of the source, copy, re-hash of
    // the copy) -- 22 GB x 4 on a fresh station. The outer SHA-256 is pinned
    // by the signed index, so a match proves every byte; the caller
    // (`acquire_verified_distribution`) then fully re-verifies the cached pack
    // (signature, component, structure, per-entry hashes) before any use.
    // The share-read-only handle denies replacement while it is being read.
    let mut input = native_packs::open_pack_file(&canonical_source)?;
    let partial = cache_root.join(format!("{}.partial", pack.sha256));
    let mut output = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&partial)
        .map_err(|error| format!("Could not create cached station pack: {error}"))?;
    let mut digest = Sha256::new();
    let mut copied = 0_u64;
    let mut buffer = vec![0_u8; 1024 * 1024];
    let copy_result = loop {
        let count = match input.read(&mut buffer) {
            Ok(count) => count,
            Err(error) => break Err(format!("Could not read station component pack: {error}")),
        };
        if count == 0 {
            break Ok(());
        }
        if let Err(error) = output.write_all(&buffer[..count]) {
            break Err(format!("Could not cache station pack: {error}"));
        }
        digest.update(&buffer[..count]);
        copied += count as u64;
    }
    .and_then(|()| {
        output
            .sync_all()
            .map_err(|error| format!("Could not flush cached station pack: {error}"))
    });
    drop(output);
    if let Err(error) = copy_result {
        let _ = fs::remove_file(&partial);
        return Err(error);
    }
    if copied != pack.bytes || format!("{:x}", digest.finalize()) != pack.sha256 {
        let _ = fs::remove_file(&partial);
        return Err(format!(
            "Native station pack outer identity does not match its signed index: {}",
            pack.component
        ));
    }
    fs::rename(&partial, &final_path)
        .map_err(|error| format!("Could not promote cached station pack: {error}"))?;
    Ok(final_path)
}

fn apply_transfer_response(
    pack: &DistributionPack,
    partial_path: &Path,
    requested_offset: u64,
    mut response: TransferResponse,
) -> Result<(), String> {
    let append = match response.status {
        206 => {
            let range = response.content_range.as_deref().ok_or_else(|| {
                "Resumed native pack response is missing Content-Range.".to_string()
            })?;
            let (start, end, total) = parse_content_range(range)?;
            if start != requested_offset || total != pack.bytes {
                return Err(format!(
                    "Native pack Content-Range does not match the requested offset or signed byte count: {range}"
                ));
            }
            let range_length = end
                .checked_sub(start)
                .and_then(|value| value.checked_add(1))
                .ok_or_else(|| "Native pack Content-Range is invalid.".to_string())?;
            if response
                .content_length
                .is_some_and(|length| length != range_length)
            {
                return Err("Native pack Content-Length does not match Content-Range.".to_string());
            }
            true
        }
        200 => {
            if response.content_range.is_some() {
                return Err(
                    "Full native pack response unexpectedly contains Content-Range.".to_string(),
                );
            }
            false
        }
        status => {
            return Err(format!(
                "Native pack server returned unexpected HTTP status {status}."
            ));
        }
    };
    let base = if append { requested_offset } else { 0 };
    let remaining = pack
        .bytes
        .checked_sub(base)
        .ok_or_else(|| "Native pack offset exceeds signed byte count.".to_string())?;
    if response
        .content_length
        .is_some_and(|length| length > remaining)
    {
        if !append {
            let _ = fs::remove_file(partial_path);
        }
        return Err(
            "Native pack response exceeds the signed byte count before writing.".to_string(),
        );
    }

    if partial_path.exists() {
        regular_file_size(partial_path, "partial native pack")?;
    }
    let mut options = OpenOptions::new();
    options.create(true).write(true);
    if append {
        options.append(true);
    } else {
        options.truncate(true);
    }
    let mut output = options
        .open(partial_path)
        .map_err(|error| format!("Could not open partial native pack: {error}"))?;
    let mut response_bytes = 0_u64;
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let count = response
            .body
            .read(&mut buffer)
            .map_err(|error| format!("Could not read native pack response: {error}"))?;
        if count == 0 {
            break;
        }
        response_bytes = response_bytes
            .checked_add(count as u64)
            .ok_or_else(|| "Native pack response byte count overflowed.".to_string())?;
        if response_bytes > remaining {
            drop(output);
            let _ = fs::remove_file(partial_path);
            return Err("Native pack response exceeds the signed byte count.".to_string());
        }
        output
            .write_all(&buffer[..count])
            .map_err(|error| format!("Could not write partial native pack: {error}"))?;
    }
    output
        .sync_all()
        .map_err(|error| format!("Could not flush partial native pack: {error}"))?;
    drop(output);
    if response
        .content_length
        .is_some_and(|length| length != response_bytes)
    {
        return Err("Native pack response ended before its Content-Length.".to_string());
    }
    let observed = regular_file_size(partial_path, "partial native pack")?;
    if observed != pack.bytes {
        return Err(format!(
            "Native pack response is incomplete: received {observed} of {} signed bytes.",
            pack.bytes
        ));
    }
    Ok(())
}

// `pub(crate)`: `component_acquisition.rs` reuses this exact Content-Range
// parser for its own generic (non-pack) resumable downloads rather than
// re-implementing the same "bytes start-end/total" grammar a second time.
pub(crate) fn parse_content_range(value: &str) -> Result<(u64, u64, u64), String> {
    let remainder = value
        .strip_prefix("bytes ")
        .ok_or_else(|| "Native pack Content-Range is invalid.".to_string())?;
    let (range, total) = remainder
        .split_once('/')
        .ok_or_else(|| "Native pack Content-Range is invalid.".to_string())?;
    let (start, end) = range
        .split_once('-')
        .ok_or_else(|| "Native pack Content-Range is invalid.".to_string())?;
    let start = start
        .parse::<u64>()
        .map_err(|_| "Native pack Content-Range start is invalid.".to_string())?;
    let end = end
        .parse::<u64>()
        .map_err(|_| "Native pack Content-Range end is invalid.".to_string())?;
    let total = total
        .parse::<u64>()
        .map_err(|_| "Native pack Content-Range total is invalid.".to_string())?;
    if end < start || end >= total || total == 0 {
        return Err("Native pack Content-Range bounds are invalid.".to_string());
    }
    Ok((start, end, total))
}

fn regular_file_size(path: &Path, label: &str) -> Result<u64, String> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("Could not inspect {label}: {error}"))?;
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return Err(format!("{label} must be a regular non-link file."));
    }
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err(format!("{label} must not be a reparse point."));
        }
    }
    Ok(metadata.len())
}

fn file_matches(path: &Path, expected_bytes: u64, expected_sha256: &str) -> Result<bool, String> {
    if regular_file_size(path, "cached native pack")? != expected_bytes {
        return Ok(false);
    }
    let mut input =
        File::open(path).map_err(|error| format!("Could not open cached native pack: {error}"))?;
    let mut digest = Sha256::new();
    let mut observed = 0_u64;
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let count = input
            .read(&mut buffer)
            .map_err(|error| format!("Could not hash cached native pack: {error}"))?;
        if count == 0 {
            break;
        }
        observed += count as u64;
        digest.update(&buffer[..count]);
    }
    Ok(observed == expected_bytes && format!("{:x}", digest.finalize()) == expected_sha256)
}

fn validate_urls(kind: &str, component: &str, urls: &[String]) -> Result<(), String> {
    if kind == "station-index" {
        if !urls.is_empty() {
            return Err("Native station index must not contain network locations.".to_string());
        }
        return Ok(());
    }
    if urls.is_empty() {
        return Err(format!(
            "Native online index requires an HTTPS location: {component}"
        ));
    }
    let mut seen = BTreeSet::new();
    for location in urls {
        if !location.is_ascii() || !seen.insert(location) {
            return Err(format!(
                "Native online index contains an invalid HTTPS location: {component}"
            ));
        }
        let parsed = Url::parse(location).map_err(|_| {
            format!("Native online index contains an invalid HTTPS location: {component}")
        })?;
        if parsed.scheme() != "https"
            || parsed.host_str().is_none()
            || !parsed.username().is_empty()
            || parsed.password().is_some()
            || parsed.fragment().is_some()
            || parsed.path().is_empty()
            || parsed.path() == "/"
        {
            return Err(format!(
                "Native online index requires unambiguous HTTPS locations: {component}"
            ));
        }
    }
    Ok(())
}

fn require_identity(field: &str, value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.trim() != value
        || !value
            .bytes()
            .all(|character| (0x20..=0x7e).contains(&character))
    {
        return Err(format!(
            "Native distribution identity field is invalid: {field}"
        ));
    }
    Ok(())
}

fn require_component(value: &str) -> Result<(), String> {
    let valid = !value.is_empty()
        && value.len() <= 64
        && value.is_ascii()
        && value.bytes().all(|character| {
            character.is_ascii_lowercase() || character.is_ascii_digit() || character == b'-'
        })
        && value
            .as_bytes()
            .first()
            .is_some_and(u8::is_ascii_alphanumeric)
        && value
            .as_bytes()
            .last()
            .is_some_and(u8::is_ascii_alphanumeric);
    if !valid {
        return Err(format!(
            "Native distribution component identity is invalid: {value}"
        ));
    }
    Ok(())
}

fn safe_pack_filename(value: &str) -> Result<String, String> {
    let reserved = BTreeSet::from([
        "aux", "clock$", "con", "nul", "prn", "com1", "com2", "com3", "com4", "com5", "com6",
        "com7", "com8", "com9", "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8",
        "lpt9",
    ]);
    let forbidden = |character: u8| {
        character < 0x20
            || character > 0x7e
            || matches!(
                character,
                b'<' | b'>' | b':' | b'"' | b'/' | b'\\' | b'|' | b'?' | b'*'
            )
    };
    let stem = value.split('.').next().unwrap_or_default().to_lowercase();
    if value.is_empty()
        || value.trim() != value
        || value.ends_with([' ', '.'])
        || value.bytes().any(forbidden)
        || reserved.contains(stem.as_str())
        || !value.to_lowercase().ends_with(".ccpack")
    {
        return Err("Native distribution pack filename is unsafe.".to_string());
    }
    Ok(value.to_string())
}

fn is_lower_hex_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|character| character.is_ascii_digit() || (b'a'..=b'f').contains(&character))
}

fn component_sort_key(component: &str) -> (u8, usize, String) {
    match REQUIRED_COMPONENTS
        .iter()
        .position(|required| *required == component)
    {
        Some(index) => (0, index, String::new()),
        None => (1, 0, component.to_string()),
    }
}

pub(crate) fn canonical_json(value: &Value) -> Result<String, String> {
    let mut output = String::new();
    write_canonical_json(value, &mut output)?;
    output.push('\n');
    Ok(output)
}

fn write_canonical_json(value: &Value, output: &mut String) -> Result<(), String> {
    match value {
        Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_) => {
            output.push_str(
                &serde_json::to_string(value).map_err(|error| {
                    format!("Could not canonicalize distribution JSON: {error}")
                })?,
            );
        }
        Value::Array(items) => {
            output.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                write_canonical_json(item, output)?;
            }
            output.push(']');
        }
        Value::Object(items) => {
            output.push('{');
            let sorted: BTreeMap<_, _> = items.iter().collect();
            for (index, (key, item)) in sorted.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                output.push_str(&serde_json::to_string(key).map_err(|error| {
                    format!("Could not canonicalize distribution key: {error}")
                })?);
                output.push(':');
                write_canonical_json(item, output)?;
            }
            output.push('}');
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        download_pack_with, verify_distribution_bytes, DistributionPack, TransferResponse,
        VerifiedDistribution,
    };
    use crate::native_packs::PackTrust;
    use base64::engine::general_purpose::STANDARD as BASE64;
    use base64::Engine as _;
    use ed25519_dalek::{Signer, SigningKey};
    use serde_json::{json, Value};
    use sha2::{Digest, Sha256};
    use std::io::{Cursor, Write as _};
    use std::path::PathBuf;

    fn signed_index(kind: &str) -> (Vec<u8>, PackTrust) {
        let key = SigningKey::from_bytes(&[7_u8; 32]);
        let trust = PackTrust {
            key_id: "development-test-key".to_string(),
            public_key: key.verifying_key(),
        };
        let urls = |name: &str| {
            if kind == "channel-index" {
                json!([format!("https://downloads.civiccast.org/native/{name}")])
            } else {
                json!([])
            }
        };
        let manifest = json!({
            "schema_version": 1,
            "product": "civiccast-native",
            "kind": kind,
            "channel": "beta",
            "product_version": "1.0.0-rc15",
            "compatible_core": "1.0.0-rc15",
            "signing_key_id": "development-test-key",
            "created_epoch": 1700000000,
            "packs": [
                {
                    "component": "core",
                    "filename": "core.ccpack",
                    "bytes": 1,
                    "sha256": "00".repeat(32),
                    "required": true,
                    "urls": urls("core.ccpack"),
                },
                {
                    "component": "captions-floor",
                    "filename": "captions-floor.ccpack",
                    "bytes": 2,
                    "sha256": "11".repeat(32),
                    "required": true,
                    "urls": urls("captions-floor.ccpack"),
                },
                {
                    "component": "summary-gemma4-12b",
                    "filename": "summary-12b.ccpack",
                    "bytes": 3,
                    "sha256": "22".repeat(32),
                    "required": true,
                    "urls": urls("summary-12b.ccpack"),
                },
                {
                    "component": "summary-gemma4-e4b",
                    "filename": "summary-e4b.ccpack",
                    "bytes": 4,
                    "sha256": "33".repeat(32),
                    "required": true,
                    "urls": urls("summary-e4b.ccpack"),
                },
                {
                    "component": "translation-translategemma-4b",
                    "filename": "translation.ccpack",
                    "bytes": 5,
                    "sha256": "44".repeat(32),
                    "required": true,
                    "urls": urls("translation.ccpack"),
                },
                {
                    "component": "captions-whistle",
                    "filename": "captions-whistle.ccpack",
                    "bytes": 6,
                    "sha256": "55".repeat(32),
                    "required": true,
                    "urls": urls("captions-whistle.ccpack"),
                },
            ],
        });
        let manifest_bytes = canonical(&manifest);
        let signature = BASE64.encode(key.sign(&manifest_bytes).to_bytes());
        let envelope = json!({"manifest": manifest, "signature": signature});
        (canonical(&envelope), trust)
    }

    fn canonical(value: &Value) -> Vec<u8> {
        let mut rendered = serde_json::to_string(value).expect("JSON");
        rendered.push('\n');
        rendered.into_bytes()
    }

    #[test]
    fn signed_channel_index_requires_all_mandatory_packs() {
        let (bytes, trust) = signed_index("channel-index");
        let verified = verify_distribution_bytes(
            &bytes,
            &trust,
            Some("channel-index"),
            Some("beta"),
            Some("1.0.0-rc15"),
            Some("1.0.0-rc15"),
        )
        .expect("valid signed channel index");

        assert_eq!(verified.packs.len(), 6);
        assert_eq!(verified.packs[1].component, "captions-floor");
        assert!(verified.packs.iter().all(|pack| pack.required));
    }

    #[test]
    fn cryptographically_valid_optional_floor_caption_pack_is_rejected() {
        let (bytes, trust) = signed_index("channel-index");
        let mut envelope: Value = serde_json::from_slice(&bytes).expect("envelope");
        envelope["manifest"]["packs"][1]["required"] = Value::Bool(false);
        let key = SigningKey::from_bytes(&[7_u8; 32]);
        envelope["signature"] =
            Value::String(BASE64.encode(key.sign(&canonical(&envelope["manifest"])).to_bytes()));

        let error =
            verify_distribution_bytes(&canonical(&envelope), &trust, None, None, None, None)
                .expect_err("captions cannot be optional");
        assert!(error.contains("must be required"));
    }

    #[test]
    fn large_v3_caption_pack_may_be_legitimately_optional() {
        // Owner decision (2026-08-07): large-v3 is an optional quality
        // add-on, not mandatory -- unlike the floor tier tested above, a
        // signed index MAY carry large-v3 with `required: false` and it
        // must verify cleanly.
        let (bytes, trust) = signed_index("channel-index");
        let mut envelope: Value = serde_json::from_slice(&bytes).expect("envelope");
        envelope["manifest"]["packs"]
            .as_array_mut()
            .expect("packs array")
            .push(json!({
                "component": "captions-large-v3",
                "filename": "captions-large-v3.ccpack",
                "bytes": 6,
                "sha256": "55".repeat(32),
                "required": false,
                "urls": json!(["https://downloads.civiccast.org/native/captions-large-v3.ccpack"]),
            }));
        let key = SigningKey::from_bytes(&[7_u8; 32]);
        envelope["signature"] =
            Value::String(BASE64.encode(key.sign(&canonical(&envelope["manifest"])).to_bytes()));

        let verified = verify_distribution_bytes(
            &canonical(&envelope),
            &trust,
            Some("channel-index"),
            Some("beta"),
            Some("1.0.0-rc15"),
            Some("1.0.0-rc15"),
        )
        .expect("optional large-v3 pack must verify");

        assert_eq!(verified.packs.len(), 7);
        let large_v3 = verified
            .packs
            .iter()
            .find(|pack| pack.component == "captions-large-v3")
            .expect("large-v3 pack present");
        assert!(!large_v3.required);
    }

    #[test]
    fn station_index_rejects_network_locations() {
        let (bytes, trust) = signed_index("station-index");
        let mut envelope: Value = serde_json::from_slice(&bytes).expect("envelope");
        envelope["manifest"]["packs"][0]["urls"] =
            json!(["https://downloads.civiccast.org/native/core.ccpack"]);
        let key = SigningKey::from_bytes(&[7_u8; 32]);
        envelope["signature"] =
            Value::String(BASE64.encode(key.sign(&canonical(&envelope["manifest"])).to_bytes()));

        let error = verify_distribution_bytes(
            &canonical(&envelope),
            &trust,
            Some("station-index"),
            None,
            None,
            None,
        )
        .expect_err("air-gapped index cannot contain URLs");
        assert!(error.contains("network"));
    }

    #[test]
    fn channel_index_rejects_plain_http() {
        let (bytes, trust) = signed_index("channel-index");
        let mut envelope: Value = serde_json::from_slice(&bytes).expect("envelope");
        envelope["manifest"]["packs"][0]["urls"] =
            json!(["http://downloads.civiccast.org/native/core.ccpack"]);
        let key = SigningKey::from_bytes(&[7_u8; 32]);
        envelope["signature"] =
            Value::String(BASE64.encode(key.sign(&canonical(&envelope["manifest"])).to_bytes()));

        let error = verify_distribution_bytes(
            &canonical(&envelope),
            &trust,
            Some("channel-index"),
            None,
            None,
            None,
        )
        .expect_err("online index requires HTTPS");
        assert!(error.contains("HTTPS"));
    }

    fn temporary_root(label: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "civiccast-native-distribution-{label}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).expect("create test cache");
        root
    }

    fn download_pack(bytes: &[u8]) -> DistributionPack {
        DistributionPack {
            component: "core".to_string(),
            filename: "core.ccpack".to_string(),
            bytes: bytes.len() as u64,
            sha256: format!("{:x}", Sha256::digest(bytes)),
            required: true,
            urls: vec!["https://downloads.civiccast.org/core.ccpack".to_string()],
        }
    }

    #[test]
    fn resumable_download_appends_only_a_matching_partial_response() {
        let root = temporary_root("resume");
        let expected = b"abcdef";
        let pack = download_pack(expected);
        std::fs::write(root.join(format!("{}.partial", pack.sha256)), b"abc")
            .expect("seed partial");
        let mut calls = Vec::new();

        let downloaded = download_pack_with(&pack, &root, |url, offset| {
            calls.push((url.to_string(), offset));
            Ok(TransferResponse {
                status: 206,
                content_range: Some("bytes 3-5/6".to_string()),
                content_length: Some(3),
                body: Box::new(Cursor::new(b"def".to_vec())),
            })
        })
        .expect("resume succeeds");

        assert_eq!(calls, vec![(pack.urls[0].clone(), 3)]);
        assert_eq!(std::fs::read(&downloaded).expect("download"), expected);
        assert!(!root.join(format!("{}.partial", pack.sha256)).exists());
        std::fs::remove_dir_all(root).expect("clean test cache");
    }

    #[test]
    fn server_ignoring_range_restarts_from_zero_without_duplicate_bytes() {
        let root = temporary_root("range-ignored");
        let expected = b"abcdef";
        let pack = download_pack(expected);
        std::fs::write(root.join(format!("{}.partial", pack.sha256)), b"abc")
            .expect("seed partial");

        let downloaded = download_pack_with(&pack, &root, |_url, offset| {
            assert_eq!(offset, 3);
            Ok(TransferResponse {
                status: 200,
                content_range: None,
                content_length: Some(expected.len() as u64),
                body: Box::new(Cursor::new(expected.to_vec())),
            })
        })
        .expect("restart succeeds");

        assert_eq!(std::fs::read(&downloaded).expect("download"), expected);
        std::fs::remove_dir_all(root).expect("clean test cache");
    }

    #[test]
    fn mismatched_content_range_is_rejected_before_writing() {
        let root = temporary_root("bad-range");
        let expected = b"abcdef";
        let pack = download_pack(expected);
        let partial = root.join(format!("{}.partial", pack.sha256));
        std::fs::write(&partial, b"abc").expect("seed partial");

        let error = download_pack_with(&pack, &root, |_url, _offset| {
            Ok(TransferResponse {
                status: 206,
                content_range: Some("bytes 2-4/6".to_string()),
                content_length: Some(3),
                body: Box::new(Cursor::new(b"def".to_vec())),
            })
        })
        .expect_err("wrong range must fail");

        assert!(error.contains("Content-Range"));
        assert_eq!(std::fs::read(&partial).expect("untouched partial"), b"abc");
        std::fs::remove_dir_all(root).expect("clean test cache");
    }

    #[test]
    fn complete_hash_mismatch_is_deleted_and_never_promoted() {
        let root = temporary_root("bad-hash");
        let expected = b"abcdef";
        let pack = download_pack(expected);

        let error = download_pack_with(&pack, &root, |_url, _offset| {
            Ok(TransferResponse {
                status: 200,
                content_range: None,
                content_length: Some(6),
                body: Box::new(Cursor::new(b"abcdeg".to_vec())),
            })
        })
        .expect_err("bad hash must fail");

        assert!(error.contains("SHA-256"));
        assert!(!root.join(format!("{}.partial", pack.sha256)).exists());
        assert!(!root.join(format!("{}.ccpack", pack.sha256)).exists());
        std::fs::remove_dir_all(root).expect("clean test cache");
    }

    #[test]
    fn response_larger_than_signed_size_is_bounded_and_rejected() {
        let root = temporary_root("oversize");
        let expected = b"abcdef";
        let pack = download_pack(expected);

        let error = download_pack_with(&pack, &root, |_url, _offset| {
            Ok(TransferResponse {
                status: 200,
                content_range: None,
                content_length: Some(7),
                body: Box::new(Cursor::new(b"abcdefg".to_vec())),
            })
        })
        .expect_err("oversize response must fail");

        assert!(error.contains("signed byte count"));
        assert!(!root.join(format!("{}.partial", pack.sha256)).exists());
        std::fs::remove_dir_all(root).expect("clean test cache");
    }

    // ---- K1 follow-up: station-bundle publisher round-trip ----
    //
    // `scripts/build_native_station_bundle.py` is the new (Python) publisher
    // that assembles a signed `station-index.json` plus every component
    // `.ccpack`, flat in one directory, for side-loading at
    // `$EXEDIR\station`. Invoking that Python script from a `cargo test` run
    // has no precedent anywhere in this test suite (checked), so this proves
    // the SAME schema instead: a fixture built directly in Rust, shaped
    // byte-for-byte like that publisher's real output (same component set,
    // same canonical pack-entry order, same station-index "no URLs" rule,
    // same real signed `.ccpack` files -- not placeholders), consumed by
    // `acquire_station_distribution` end to end. The publisher's own
    // `tests/native/test_build_native_station_bundle.py` separately proves
    // the Python side actually emits this exact shape (component set,
    // order, per-pack self-verification via `verify_native_pack`).

    /// Mirrors `native_pack_staging.rs`'s own `build_signed_pack` test
    /// helper (same manifest shape, same `native_packs::canonical_json`
    /// signing, same ZIP layout) -- a real, verifiable `.ccpack`, not a
    /// fake placeholder file, so `acquire_station_distribution`'s real
    /// `native_packs::verify_pack` call has something genuine to check.
    fn build_signed_pack(
        pack_path: &std::path::Path,
        signing_key: &SigningKey,
        component: &str,
        payload: &[(&str, &[u8])],
    ) -> (u64, String) {
        build_signed_pack_with_identity(
            pack_path,
            signing_key,
            component,
            "1.0.0-rc15",
            "1.0.0-rc15",
            payload,
        )
    }

    /// Same pack, with the identity pair spelled out -- the shape
    /// `scripts/build_native_station_bundle.py` actually emits, where MODEL
    /// packs declare the stable `station-models-1` identity and only `core`
    /// carries the product version. See
    /// [`super::pack_identity_expectations`].
    fn build_signed_pack_with_identity(
        pack_path: &std::path::Path,
        signing_key: &SigningKey,
        component: &str,
        product_version: &str,
        compatible_core: &str,
        payload: &[(&str, &[u8])],
    ) -> (u64, String) {
        let mut files_json = Vec::new();
        let mut total_bytes = 0_u64;
        for (name, bytes) in payload {
            files_json.push(json!({
                "path": name,
                "bytes": bytes.len(),
                "sha256": format!("{:x}", Sha256::digest(bytes)),
            }));
            total_bytes += bytes.len() as u64;
        }
        let manifest_value = json!({
            "schema_version": 1,
            "product": "civiccast-native",
            "component": component,
            "product_version": product_version,
            "compatible_core": compatible_core,
            "signing_key_id": "development-test-key",
            "file_count": payload.len(),
            "total_bytes": total_bytes,
            "files": files_json,
            "metadata": {},
        });
        let manifest_bytes = crate::native_packs::canonical_json(&manifest_value)
            .expect("canonicalize test pack manifest")
            .into_bytes();
        let signature = signing_key.sign(&manifest_bytes);
        let signature_b64 = BASE64.encode(signature.to_bytes());

        let file = std::fs::File::create(pack_path).expect("create pack file");
        let mut writer = zip::ZipWriter::new(file);
        let options =
            zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
        writer.start_file("manifest.json", options).expect("start manifest entry");
        writer.write_all(&manifest_bytes).expect("write manifest");
        writer.start_file("manifest.sig", options).expect("start signature entry");
        writer.write_all(signature_b64.as_bytes()).expect("write signature");
        for (name, bytes) in payload {
            writer
                .start_file(format!("payload/{name}"), options)
                .expect("start payload entry");
            writer.write_all(bytes).expect("write payload bytes");
        }
        writer.finish().expect("finish pack zip");

        let outer_bytes = std::fs::read(pack_path).expect("read finished pack");
        (outer_bytes.len() as u64, format!("{:x}", Sha256::digest(&outer_bytes)))
    }

    /// The exact `station-index.json` manifest+entries
    /// `scripts/build_native_station_bundle.py::_build_station_index` emits
    /// for `components` (canonical order, `required` flag, empty `urls` --
    /// see that function's own doc), signed the same way. Shared by both
    /// tests below so the "index schema" and "full acquisition" proofs stay
    /// against the identical fixture, never two subtly different ones.
    fn publisher_shaped_envelope(
        key: &SigningKey,
        pack_entries: Vec<Value>,
    ) -> (Value, Vec<u8>) {
        let manifest = json!({
            "schema_version": 1,
            "product": "civiccast-native",
            "kind": "station-index",
            "channel": "beta",
            "product_version": "1.0.0-rc15",
            "compatible_core": "1.0.0-rc15",
            "signing_key_id": "development-test-key",
            "created_epoch": 1_700_000_000,
            "packs": pack_entries,
        });
        let manifest_bytes = canonical(&manifest);
        let signature = BASE64.encode(key.sign(&manifest_bytes).to_bytes());
        let envelope = json!({"manifest": manifest, "signature": signature});
        let envelope_bytes = canonical(&envelope);
        (envelope, envelope_bytes)
    }

    fn pack_entry(component: &str, bytes: u64, sha256: &str) -> Value {
        json!({
            "component": component,
            "filename": format!("{component}.ccpack"),
            "bytes": bytes,
            "sha256": sha256,
            "required": true,
            // A station (air-gapped) index carries no network locations --
            // the same rule build_native_station_bundle.py's
            // _build_station_index enforces on the publisher side.
            "urls": [],
        })
    }

    #[test]
    fn publisher_shaped_station_index_passes_schema_and_signature_verification() {
        // Proves the LAYER of acquire_station_distribution that is fully
        // fixture-testable: verify_distribution_bytes never opens or
        // verifies a single .ccpack -- it validates only the signed index's
        // OWN schema, signature, canonical component order, `required`
        // flags, and (for a station index) that no pack carries a network
        // location. This is "the contract" this slice's publisher was built
        // against, and it round-trips cleanly with placeholder outer
        // bytes/hashes -- no real model weights needed to prove this much.
        let key = SigningKey::from_bytes(&[7_u8; 32]);
        let trust = PackTrust {
            key_id: "development-test-key".to_string(),
            public_key: key.verifying_key(),
        };
        let pack_entries = vec![
            pack_entry("core", 11, &"00".repeat(32)),
            pack_entry("captions-floor", 22, &"11".repeat(32)),
            pack_entry("summary-gemma4-12b", 33, &"22".repeat(32)),
            pack_entry("summary-gemma4-e4b", 44, &"33".repeat(32)),
            pack_entry("translation-translategemma-4b", 55, &"44".repeat(32)),
            pack_entry("captions-whistle", 66, &"55".repeat(32)),
        ];
        let (_manifest, envelope_bytes) = publisher_shaped_envelope(&key, pack_entries);

        let verified = verify_distribution_bytes(
            &envelope_bytes,
            &trust,
            Some("station-index"),
            Some("beta"),
            Some("1.0.0-rc15"),
            Some("1.0.0-rc15"),
        )
        .expect("a publisher-shaped station index must verify");

        assert_eq!(verified.packs.len(), 6);
        assert_eq!(verified.packs[1].component, "captions-floor");
        assert!(verified.packs.iter().all(|pack| pack.required));
        assert!(verified.packs.iter().all(|pack| pack.urls.is_empty()));
    }

    #[test]
    fn acquire_station_distribution_rejects_an_index_missing_required_whistle_pack() {
        // This fixture contains five signed component packs, while the
        // current station contract requires six including captions-whistle.
        // The acquisition path must reject the incomplete index before it
        // downloads or caches any pack. A complete fixture cannot safely
        // forge the pinned Ollama model bytes or Whistle payload hashes.
        let key = SigningKey::from_bytes(&[7_u8; 32]);
        let trust = PackTrust {
            key_id: "development-test-key".to_string(),
            public_key: key.verifying_key(),
        };
        let root = std::env::temp_dir().join(format!(
            "civiccast-station-bundle-roundtrip-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        // "station/" -- the exact side-load directory shape
        // ($EXEDIR\station) nsis-hooks-bootstrap.nsh's K1 wiring points
        // --civiccast-import-station at.
        let media_root = root.join("station");
        std::fs::create_dir_all(&media_root).expect("create station media root");

        // Canonical order with the final mandatory Whistle component omitted.
        let components: [(&str, &str, &[(&str, &[u8])]); 5] = [
            (
                "core",
                "core.ccpack",
                &[("NOTICE.txt", b"placeholder-only")],
            ),
            (
                "captions-floor",
                "captions-floor.ccpack",
                &[(
                    "models/faster-whisper-medium/model.bin",
                    b"floor-model-bytes",
                )],
            ),
            (
                "summary-gemma4-12b",
                "summary-gemma4-12b.ccpack",
                &[("blobs/sha256-twelve", b"twelve")],
            ),
            (
                "summary-gemma4-e4b",
                "summary-gemma4-e4b.ccpack",
                &[("blobs/sha256-efficient", b"efficient")],
            ),
            (
                "translation-translategemma-4b",
                "translation-translategemma-4b.ccpack",
                &[("blobs/sha256-translate", b"translate")],
            ),
        ];

        // Keep the real station pack identities so the expected refusal is
        // specifically the missing required component, not a version mismatch.
        let mut pack_entries = Vec::new();
        for (component, filename, payload) in components {
            let pack_path = media_root.join(filename);
            let (product_version, compatible_core) = if component == "core" {
                ("1.0.0-rc15", "1.0.0-rc15")
            } else {
                ("station-models-1", "station-models-1")
            };
            let (bytes, sha256) = build_signed_pack_with_identity(
                &pack_path,
                &key,
                component,
                product_version,
                compatible_core,
                payload,
            );
            pack_entries.push(pack_entry(component, bytes, &sha256));
        }
        let (_manifest, envelope_bytes) = publisher_shaped_envelope(&key, pack_entries);
        let index_path = media_root.join("station-index.json");
        std::fs::write(&index_path, &envelope_bytes).expect("write station index");

        let cache_root = root.join("cache");
        let error = super::acquire_station_distribution(
            &index_path,
            &cache_root,
            &trust,
            "beta",
            "1.0.0-rc15",
            "1.0.0-rc15",
        )
        .expect_err("an index missing the mandatory Whistle pack must fail closed");

        assert!(
            error.contains("captions-whistle"),
            "the refusal must identify the missing Whistle component, got: {error}"
        );
        assert!(
            !cache_root.exists(),
            "an incomplete index must be rejected before any pack is cached"
        );

        std::fs::remove_dir_all(&root).expect("clean roundtrip root");
    }

    #[test]
    fn station_index_accepts_a_model_pack_with_the_stable_identity_but_still_pins_core_to_the_product_version(
    ) {
        // The trust-boundary change itself: a station index published for
        // product 1.0.0-rc15 references MODEL packs that declare the stable
        // cross-version identity `station-models-1` (what
        // `scripts/build_native_station_bundle.py` emits, so the same
        // reviewed model set keeps the same SHA-256 across candidates and an
        // activated station can reuse its ~21 GB per-SHA cache). Those packs
        // must verify; `core` -- which really is per-version -- must still be
        // refused when its declared version is not the index's.
        let key = SigningKey::from_bytes(&[7_u8; 32]);
        let trust = PackTrust {
            key_id: "development-test-key".to_string(),
            public_key: key.verifying_key(),
        };
        let (index_bytes, _) = signed_index("station-index");
        let index = verify_distribution_bytes(
            &index_bytes,
            &trust,
            Some("station-index"),
            Some("beta"),
            Some("1.0.0-rc15"),
            Some("1.0.0-rc15"),
        )
        .expect("station index must verify");

        // The rule, stated: model packs are unpinned on version, core is not.
        assert_eq!(
            super::pack_identity_expectations(&index, "captions-floor"),
            (None, None)
        );
        assert_eq!(
            super::pack_identity_expectations(&index, "core"),
            (Some("1.0.0-rc15"), Some("1.0.0-rc15"))
        );

        let root =
            std::env::temp_dir().join(format!("civiccast-station-identity-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).expect("create identity fixture root");

        // captions-floor declaring `station-models-1` against a 1.0.0-rc15
        // index: accepted, because the index pins it by SHA-256 + bytes.
        let floor_path = root.join("captions-floor.ccpack");
        build_signed_pack_with_identity(
            &floor_path,
            &key,
            "captions-floor",
            "station-models-1",
            "station-models-1",
            &[(
                "models/faster-whisper-medium/model.bin",
                b"floor-model-bytes",
            )],
        );
        let (floor_version, floor_core) =
            super::pack_identity_expectations(&index, "captions-floor");
        let verified = crate::native_packs::verify_pack(
            &floor_path,
            &trust,
            Some("captions-floor"),
            floor_version,
            floor_core,
        )
        .expect(
            "a stable-identity model pack must verify against a product-versioned station index",
        );
        assert_eq!(verified.product_version, "station-models-1");

        // core declaring the WRONG version: still refused, on the same index.
        let core_path = root.join("core.ccpack");
        build_signed_pack_with_identity(
            &core_path,
            &key,
            "core",
            "9.9.9-not-this-product",
            "9.9.9-not-this-product",
            &[("NOTICE.txt", b"placeholder-only")],
        );
        let (core_version, core_core) = super::pack_identity_expectations(&index, "core");
        let error = crate::native_packs::verify_pack(
            &core_path,
            &trust,
            Some("core"),
            core_version,
            core_core,
        )
        .expect_err("core must stay pinned to the index's product version");
        assert!(
            error.contains("version mismatch"),
            "expected a version-mismatch refusal for core, got: {error}"
        );

        // The exemption is station-only: an ONLINE channel index keeps the
        // strict per-version check for every component.
        let (channel_bytes, _) = signed_index("channel-index");
        let channel_index = verify_distribution_bytes(
            &channel_bytes,
            &trust,
            Some("channel-index"),
            Some("beta"),
            Some("1.0.0-rc15"),
            Some("1.0.0-rc15"),
        )
        .expect("channel index must verify");
        assert_eq!(
            super::pack_identity_expectations(&channel_index, "captions-floor"),
            (Some("1.0.0-rc15"), Some("1.0.0-rc15"))
        );

        std::fs::remove_dir_all(&root).expect("clean identity fixture root");
    }

    #[test]
    fn a_station_pack_absent_from_the_media_is_served_from_the_cache_but_never_on_trust() {
        // The download-only upgrade: setup.exe arrives with NO `station\`
        // folder beside it, so the pack's bytes are not on the media. An
        // already-activated station holds them in its per-SHA cache and must
        // be able to reuse them -- but only after the cached bytes are
        // re-hashed against the signed index AND re-verified as a signed
        // pack. A corrupt cache entry is refused, naming both places looked.
        let key = SigningKey::from_bytes(&[7_u8; 32]);
        let trust = PackTrust {
            key_id: "development-test-key".to_string(),
            public_key: key.verifying_key(),
        };
        let root = std::env::temp_dir().join(format!(
            "civiccast-station-cache-fallback-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        let media_root = root.join("station");
        let cache_root = root.join("cache").join("packs");
        std::fs::create_dir_all(&media_root).expect("create empty media root");
        std::fs::create_dir_all(&cache_root).expect("create pack cache");

        // Build the pack somewhere else entirely, then seed it into the cache
        // under its SHA -- exactly the state a previous activation leaves
        // behind. The media directory stays empty.
        let staged = root.join("previously-activated.ccpack");
        let (bytes, sha256) = build_signed_pack_with_identity(
            &staged,
            &key,
            "captions-floor",
            "station-models-1",
            "station-models-1",
            &[(
                "models/faster-whisper-medium/model.bin",
                b"floor-model-bytes",
            )],
        );
        let cached = cache_root.join(format!("{sha256}.ccpack"));
        std::fs::copy(&staged, &cached).expect("seed the per-SHA cache");
        std::fs::remove_file(&staged).expect("remove the staged copy");

        let pack = DistributionPack {
            component: "captions-floor".to_string(),
            filename: "captions-floor.ccpack".to_string(),
            bytes,
            sha256: sha256.clone(),
            required: true,
            urls: Vec::new(),
        };
        assert!(
            !media_root.join(&pack.filename).exists(),
            "the fixture must model a download-only upgrade: nothing beside the installer"
        );

        let served = super::copy_station_pack_to_cache(&pack, &media_root, &cache_root, &trust)
            .expect("an absent media pack must be served from this station's verified cache");
        assert_eq!(served, cached);

        // Corrupt the cache entry: same name, different bytes. Existence is
        // never trust -- the refusal must name BOTH places that were looked.
        std::fs::write(&cached, b"this is not the pack you cached").expect("corrupt the cache");
        let error = super::copy_station_pack_to_cache(&pack, &media_root, &cache_root, &trust)
            .expect_err("a corrupt cache entry must never be served");
        assert!(
            error.contains("not beside the installer")
                && error.contains("not already cached on this station"),
            "the refusal must name both the missing media path and the cache path, got: {error}"
        );
        assert!(
            error.contains("captions-floor.ccpack") && error.contains(&sha256),
            "the refusal must name the actual paths, got: {error}"
        );

        // And with nothing cached at all: the same fail-closed refusal.
        std::fs::remove_file(&cached).expect("clear the cache entry");
        let error = super::copy_station_pack_to_cache(&pack, &media_root, &cache_root, &trust)
            .expect_err("an uncached, unshipped pack must fail closed");
        assert!(
            error.contains("not beside the installer")
                && error.contains("not already cached on this station"),
            "expected the same both-places refusal, got: {error}"
        );

        std::fs::remove_dir_all(&root).expect("clean cache-fallback root");
    }

    #[test]
    fn a_media_pack_is_cached_in_one_hashed_copy_and_a_tampered_one_is_never_promoted() {
        // RC4 install timing: the media copy now hashes bytes as it copies
        // instead of verifying the source, copying, and re-hashing the copy.
        // The signed index pins the outer SHA-256, so a one-byte change of the
        // same length must still be refused, and must leave neither a cached
        // pack nor a partial file behind.
        let key = SigningKey::from_bytes(&[7_u8; 32]);
        let trust = PackTrust {
            key_id: "development-test-key".to_string(),
            public_key: key.verifying_key(),
        };
        let root = std::env::temp_dir().join(format!(
            "civiccast-station-media-copy-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        let media_root = root.join("station");
        let cache_root = root.join("cache").join("packs");
        std::fs::create_dir_all(&media_root).expect("create media root");
        let media_pack = media_root.join("captions-floor.ccpack");
        let (bytes, sha256) = build_signed_pack_with_identity(
            &media_pack,
            &key,
            "captions-floor",
            "station-models-1",
            "station-models-1",
            &[(
                "models/faster-whisper-medium/model.bin",
                b"floor-model-bytes",
            )],
        );
        let media_root = media_root.canonicalize().expect("canonical media root");
        let pack = DistributionPack {
            component: "captions-floor".to_string(),
            filename: "captions-floor.ccpack".to_string(),
            bytes,
            sha256: sha256.clone(),
            required: true,
            urls: Vec::new(),
        };
        let cached = cache_root.join(format!("{sha256}.ccpack"));
        let partial = cache_root.join(format!("{sha256}.partial"));

        let served = super::copy_station_pack_to_cache(&pack, &media_root, &cache_root, &trust)
            .expect("an index-matching media pack must be cached");
        assert_eq!(served, cached);
        assert_eq!(
            std::fs::read(&cached).expect("read cached pack"),
            std::fs::read(&media_pack).expect("read media pack")
        );
        assert!(!partial.exists(), "a promoted copy leaves no partial file");

        // Same length, one byte different: must never be promoted.
        std::fs::remove_file(&cached).expect("clear cache");
        let mut tampered = std::fs::read(&media_pack).expect("read media pack");
        let last = tampered.len() - 1;
        tampered[last] ^= 0x01;
        std::fs::write(&media_pack, &tampered).expect("tamper media pack");
        let error = super::copy_station_pack_to_cache(&pack, &media_root, &cache_root, &trust)
            .expect_err("a media pack that differs from the signed index must be refused");
        assert!(
            error.contains("does not match its signed index"),
            "expected the signed-index refusal, got: {error}"
        );
        assert!(!cached.exists(), "a tampered pack must never be cached");
        assert!(
            !partial.exists(),
            "a refused copy must not leave a partial file"
        );

        // A good cached copy is reused and never overwritten by tampered media.
        std::fs::write(&media_pack, {
            let mut good = tampered.clone();
            good[last] ^= 0x01;
            good
        })
        .expect("restore media pack");
        super::copy_station_pack_to_cache(&pack, &media_root, &cache_root, &trust)
            .expect("re-cache the good pack");
        std::fs::write(&media_pack, &tampered).expect("tamper media pack again");
        let served = super::copy_station_pack_to_cache(&pack, &media_root, &cache_root, &trust)
            .expect("an already verified cache entry is served");
        assert_eq!(served, cached);
        assert!(
            super::file_matches(&cached, pack.bytes, &pack.sha256).expect("hash cached pack"),
            "the cached pack must still be the signed bytes"
        );

        std::fs::remove_dir_all(&root).expect("clean media-copy root");
    }

    #[test]
    fn station_model_components_are_derived_from_the_component_constants() {
        // `is_station_model_component` slices REQUIRED_COMPONENTS[1..] to
        // drop the per-version `core` placeholder. If `core` ever stops
        // being first, that slice would silently exempt it and pin a real
        // model pack instead -- so the assumption is pinned here rather than
        // trusted.
        assert_eq!(
            super::REQUIRED_COMPONENTS[0],
            "core",
            "core must stay first in REQUIRED_COMPONENTS: the model allowlist slices it off"
        );
        // <installer-path-audit MA-20> The loop that used to sit here was a
        // TAUTOLOGY: `is_station_model_component` is DEFINED as
        // `REQUIRED_COMPONENTS[1..].contains(&component)`, so asserting that
        // the elements of an array are in that array cannot fail. It read as
        // coverage only because the genuinely valuable first assertion sits
        // beside it. The replacement states the four model ids
        // INDEPENDENTLY, so a reordering, a rename, or a component moved in
        // or out of the model allowlist is caught by a value this test
        // asserts rather than one it re-derives.
        assert_eq!(
            super::REQUIRED_COMPONENTS,
            [
                "core",
                "captions-floor",
                "summary-gemma4-12b",
                "summary-gemma4-e4b",
                "translation-translategemma-4b",
                "captions-whistle",
            ],
            "REQUIRED_COMPONENTS changed -- update the model allowlist expectations below \
             deliberately, and check native_activation's copy and the station bundle publisher"
        );
        for component in [
            "captions-floor",
            "summary-gemma4-12b",
            "summary-gemma4-e4b",
            "translation-translategemma-4b",
            "captions-whistle",
        ] {
            assert!(
                super::is_station_model_component(component),
                "{component} must be treated as a station model component"
            );
        }
        for component in crate::native_activation::OPTIONAL_COMPONENTS {
            assert!(
                super::is_station_model_component(component),
                "{component} must be treated as a station model component"
            );
        }
        assert!(!super::is_station_model_component("core"));
    }

    #[test]
    fn the_two_required_component_lists_are_pinned_equal() {
        // <installer-path-audit MA-19> Two hand-maintained copies, a comment
        // claiming "kept in lockstep", and NOTHING binding them: a grep for
        // `native_activation::REQUIRED_COMPONENTS` across `src/*.rs` found no
        // hits. Drift makes `is_station_model_component` -- which derives the
        // model allowlist from THIS module's copy plus native_activation's
        // OPTIONAL_COMPONENTS -- disagree with what activation requires, so
        // every model pack fails the exact-version identity check and
        // activation exits 66 -> installer 123.
        assert_eq!(
            super::REQUIRED_COMPONENTS,
            crate::native_activation::REQUIRED_COMPONENTS,
            "native_distribution and native_activation disagree about the required components"
        );
    }

    #[test]
    fn a_station_index_component_outside_the_model_allowlist_keeps_the_exact_version_contract() {
        // The exemption is an allowlist, not "anything except core". A
        // future non-model component in a station bundle -- a config pack, a
        // license pack, anything genuinely per-version -- must keep the
        // exact-version contract until someone deliberately adds it to the
        // allowlist AND makes its bytes reproducible.
        let index = VerifiedDistribution {
            sha256: "ab".repeat(32),
            kind: "station-index".to_string(),
            channel: "beta".to_string(),
            product_version: "1.0.0-rc15".to_string(),
            compatible_core: "1.0.0-rc15".to_string(),
            signing_key_id: "development-test-key".to_string(),
            created_epoch: 1_700_000_000,
            packs: Vec::new(),
        };

        for component in ["station-config", "native-app-payload", "some-future-pack"] {
            assert_eq!(
                super::pack_identity_expectations(&index, component),
                (Some("1.0.0-rc15"), Some("1.0.0-rc15")),
                "{component} is not a reviewed model pack and must stay pinned to the \
                 index's product version"
            );
        }
        // ...while the reviewed model set stays exempt on the same index.
        assert_eq!(
            super::pack_identity_expectations(&index, "captions-large-v3"),
            (None, None)
        );
    }

    /// Best-effort file symlink. Returns false when this host refuses to
    /// create one (a Windows box without the Create Symbolic Links right --
    /// the same condition `tests/installer/test_native_distribution.py`
    /// skips on), so a symlink-shaped test can degrade to a junction (see
    /// [`try_junction`]) instead of failing for an unrelated reason.
    fn try_symlink_file(target: &std::path::Path, link: &std::path::Path) -> bool {
        #[cfg(windows)]
        {
            std::os::windows::fs::symlink_file(target, link).is_ok()
        }
        #[cfg(not(windows))]
        {
            std::os::unix::fs::symlink(target, link).is_ok()
        }
    }

    /// Best-effort directory JUNCTION at `link`. Unlike a symlink, a
    /// junction needs no special privilege on Windows, so this is the
    /// reparse-point shape that can actually be planted by an unprivileged
    /// attacker -- or left behind by a botched uninstall -- on the machines
    /// this installer runs on. `target` need not exist: `mklink /J` happily
    /// creates a DANGLING junction, which is exactly the media-directory
    /// case worth proving. `std` cannot create one, hence the shell out.
    #[cfg(windows)]
    fn try_junction(target: &std::path::Path, link: &std::path::Path) -> bool {
        std::process::Command::new("cmd")
            .arg("/c")
            .arg("mklink")
            .arg("/J")
            .arg(link)
            .arg(target)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
            && std::fs::symlink_metadata(link).is_ok()
    }

    #[cfg(not(windows))]
    fn try_junction(target: &std::path::Path, link: &std::path::Path) -> bool {
        // No junctions off Windows; a symlink is the equivalent shape.
        try_symlink_file(target, link)
    }

    /// Remove a planted link/junction/file at `path` without caring which
    /// shape it is: a junction is a directory entry (`remove_dir`), a file
    /// symlink is not (`remove_file`).
    fn remove_planted_link(path: &std::path::Path) {
        if std::fs::remove_file(path).is_ok() {
            return;
        }
        std::fs::remove_dir(path).expect("remove the planted link");
    }

    #[test]
    fn a_planted_link_or_directory_at_the_cache_path_is_never_served() {
        // "Cached" must mean a real, regular, signed pack file whose bytes
        // hash to what the signed index pins -- never merely "something
        // exists at that path". A directory or a link planted at
        // <cache>/<sha>.ccpack must fail closed, even when the link's target
        // is itself a perfectly valid pack.
        let key = SigningKey::from_bytes(&[7_u8; 32]);
        let trust = PackTrust {
            key_id: "development-test-key".to_string(),
            public_key: key.verifying_key(),
        };
        let root = std::env::temp_dir().join(format!(
            "civiccast-station-cache-link-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        let media_root = root.join("station");
        let cache_root = root.join("cache").join("packs");
        std::fs::create_dir_all(&media_root).expect("create empty media root");
        std::fs::create_dir_all(&cache_root).expect("create pack cache");

        let real_pack = root.join("elsewhere.ccpack");
        let (bytes, sha256) = build_signed_pack_with_identity(
            &real_pack,
            &key,
            "captions-floor",
            "station-models-1",
            "station-models-1",
            &[(
                "models/faster-whisper-medium/model.bin",
                b"floor-model-bytes",
            )],
        );
        let pack = DistributionPack {
            component: "captions-floor".to_string(),
            filename: "captions-floor.ccpack".to_string(),
            bytes,
            sha256: sha256.clone(),
            required: true,
            urls: Vec::new(),
        };
        let cached = cache_root.join(format!("{sha256}.ccpack"));

        // (a) A DIRECTORY at the cache path (portable; no symlink privilege
        // needed). This is also the shape a stray junction leaves behind.
        std::fs::create_dir(&cached).expect("plant a directory at the cache path");
        let error = super::copy_station_pack_to_cache(&pack, &media_root, &cache_root, &trust)
            .expect_err("a directory at the cache path must never be served");
        assert!(
            error.contains("not already cached on this station"),
            "expected the fail-closed both-places refusal, got: {error}"
        );
        std::fs::remove_dir(&cached).expect("clear the planted directory");

        // (b) A JUNCTION at the cache path. This is the reparse point an
        // UNPRIVILEGED process can actually plant on Windows (no Create
        // Symbolic Links right needed), so unlike the symlink case below it
        // really runs on a stock developer/CI box.
        let junction_target = root.join("junction-target");
        std::fs::create_dir_all(&junction_target).expect("create junction target");
        assert!(
            try_junction(&junction_target, &cached),
            "a junction must be plantable without privilege; if this host refuses, the \
             cache-path fail-closed check would go unproven"
        );
        let error = super::copy_station_pack_to_cache(&pack, &media_root, &cache_root, &trust)
            .expect_err("a junction at the cache path must never be served");
        assert!(
            error.contains("not already cached on this station"),
            "expected the fail-closed both-places refusal, got: {error}"
        );
        assert!(
            std::fs::symlink_metadata(&cached).is_ok(),
            "the refusal must not have quietly replaced the planted junction"
        );
        remove_planted_link(&cached);

        // (c) A SYMLINK at the cache path whose target IS a valid pack.
        // Only runs where symlink creation is permitted; (a) and (b) already
        // prove the path is not trusted for merely existing.
        if try_symlink_file(&real_pack, &cached) {
            let error = super::copy_station_pack_to_cache(&pack, &media_root, &cache_root, &trust)
                .expect_err("a link at the cache path must never be served, even to a valid pack");
            assert!(
                !error.is_empty(),
                "the refusal must carry an operator-readable reason"
            );
            assert!(
                std::fs::symlink_metadata(&cached)
                    .expect("the planted link must still be there")
                    .file_type()
                    .is_symlink(),
                "the refusal must not have quietly replaced the planted link"
            );
            remove_planted_link(&cached);
        }

        std::fs::remove_dir_all(&root).expect("clean cache-link root");
    }

    #[test]
    fn a_dangling_media_link_is_not_served_and_falls_through_to_the_verified_cache() {
        // A dangling `<component>.ccpack` link in the media directory is not
        // a pack. It must never be served as one; the run falls through to
        // the per-SHA cache, which is re-verified in full (bytes, digest,
        // signature, component) before anything is served -- and fails
        // closed, naming both places, when that cache cannot satisfy it.
        let key = SigningKey::from_bytes(&[7_u8; 32]);
        let trust = PackTrust {
            key_id: "development-test-key".to_string(),
            public_key: key.verifying_key(),
        };
        let root = std::env::temp_dir().join(format!(
            "civiccast-station-media-link-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        let media_root = root.join("station");
        let cache_root = root.join("cache").join("packs");
        std::fs::create_dir_all(&media_root).expect("create media root");
        std::fs::create_dir_all(&cache_root).expect("create pack cache");

        let staged = root.join("previously-activated.ccpack");
        let (bytes, sha256) = build_signed_pack_with_identity(
            &staged,
            &key,
            "captions-floor",
            "station-models-1",
            "station-models-1",
            &[(
                "models/faster-whisper-medium/model.bin",
                b"floor-model-bytes",
            )],
        );
        let pack = DistributionPack {
            component: "captions-floor".to_string(),
            filename: "captions-floor.ccpack".to_string(),
            bytes,
            sha256: sha256.clone(),
            required: true,
            urls: Vec::new(),
        };

        // A DANGLING link where the media pack would be. A symlink where the
        // host permits one; otherwise a dangling JUNCTION, which needs no
        // privilege -- so this shape is genuinely exercised everywhere, not
        // quietly skipped.
        let media_pack = media_root.join(&pack.filename);
        let dangling_target = root.join("this-target-does-not-exist");
        assert!(
            try_symlink_file(&dangling_target, &media_pack)
                || try_junction(&dangling_target, &media_pack),
            "neither a symlink nor a junction could be planted; the dangling-media-entry \
             case would go unproven"
        );
        assert!(
            std::fs::symlink_metadata(&media_pack).is_ok(),
            "the planted dangling entry must exist as a directory entry"
        );

        // With nothing usable in the cache: fail closed naming both places.
        // Critically NOT "resolved the link and served whatever it pointed at".
        let error = super::copy_station_pack_to_cache(&pack, &media_root, &cache_root, &trust)
            .expect_err("a dangling media link must never be served as a pack");
        assert!(
            error.contains("not beside the installer")
                && error.contains("not already cached on this station"),
            "expected the both-places refusal, got: {error}"
        );

        // Now seed the cache properly: the run is served from the CACHE, and
        // never from the media path the dangling link occupies.
        let cached = cache_root.join(format!("{sha256}.ccpack"));
        std::fs::copy(&staged, &cached).expect("seed the per-SHA cache");
        let served = super::copy_station_pack_to_cache(&pack, &media_root, &cache_root, &trust)
            .expect("the verified cache must satisfy a pack whose media entry is a dangling link");
        assert_eq!(served, cached);
        assert_ne!(served, media_pack);

        std::fs::remove_dir_all(&root).expect("clean media-link root");
    }
}
