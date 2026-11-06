#!/usr/bin/env python3
"""Apply portable OSCrypt changes to an already-patched Chromium source tree.

The build-specific wrapping key is read only from PORTABLE_OSCRYPT_KEY. It is
embedded into the two compiled OSCrypt providers; it is never printed or saved
as a separate key file.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

KEY_ENV = "PORTABLE_OSCRYPT_KEY"

SYNC_REL = Path("components/os_crypt/sync/os_crypt_win.cc")
ASYNC_REL = Path("components/os_crypt/async/browser/dpapi_key_provider.cc")
ASYNC_BUILD_REL = Path("components/os_crypt/async/browser/BUILD.gn")
CHROME_MAIN_REL = Path("chrome/browser/chrome_browser_main_win.cc")

SYNC_PREFIX = '''constexpr char kPortableKeyPrefix[] = "PORTABLE";
constexpr char kPortableKeyAad[] = "ungoogled-chromium-portable-oscrypt-key-v1";
constexpr unsigned char kPortableOsCryptWrappingKey[] = {
    {key_values}
};
static_assert(sizeof(kPortableOsCryptWrappingKey) == kKeyLength,
              "Portable OSCrypt wrapping key must be 256 bits.");
'''

SYNC_HELPERS = r'''// Wrap the per-profile AES key with the stable build-specific portable key.
bool EncryptStringWithPortableKey(const std::string& plaintext,
                                  std::string* ciphertext) {
  const std::string wrapping_key(
      reinterpret_cast<const char*>(kPortableOsCryptWrappingKey),
      sizeof(kPortableOsCryptWrappingKey));
  crypto::Aead aead(crypto::Aead::AES_256_GCM);
  aead.Init(&wrapping_key);

  std::string nonce(kNonceLength, '\0');
  crypto::RandBytes(base::as_writable_byte_span(nonce));

  std::string encrypted_data;
  if (!aead.Seal(plaintext, nonce, kPortableKeyAad, &encrypted_data))
    return false;

  ciphertext->assign(kPortableKeyPrefix);
  ciphertext->append(nonce);
  ciphertext->append(encrypted_data);
  return true;
}

bool DecryptStringWithPortableKey(const std::string& ciphertext,
                                  std::string* plaintext) {
  constexpr size_t kAeadTagLength = 128 / 8;
  constexpr size_t kPrefixLength = sizeof(kPortableKeyPrefix) - 1;
  if (ciphertext.size() < kPrefixLength + kNonceLength + kAeadTagLength)
    return false;

  const std::string wrapping_key(
      reinterpret_cast<const char*>(kPortableOsCryptWrappingKey),
      sizeof(kPortableOsCryptWrappingKey));
  crypto::Aead aead(crypto::Aead::AES_256_GCM);
  aead.Init(&wrapping_key);

  const std::string nonce = ciphertext.substr(kPrefixLength, kNonceLength);
  const std::string encrypted_data =
      ciphertext.substr(kPrefixLength + kNonceLength);
  return aead.Open(encrypted_data, nonce, kPortableKeyAad, plaintext);
}
'''

SYNC_ENCRYPT_AND_STORE = r'''bool EncryptAndStoreKey(const std::string& key, PrefService* local_state) {
  std::string encrypted_key;
  if (!EncryptStringWithPortableKey(key, &encrypted_key))
    return false;

  const std::string base64_key = base::Base64Encode(encrypted_key);
  local_state->SetString(kOsCryptEncryptedKeyPrefName, base64_key);
  return true;
}'''

SYNC_INIT = r'''bool OSCryptImpl::Init(PrefService* local_state) {
  // Never replace an existing key when its format or authentication is invalid.
  switch (InitWithExistingKey(local_state)) {
    case OSCrypt::kSuccess:
      return true;
    case OSCrypt::kKeyDoesNotExist:
      break;
    case OSCrypt::kInvalidKeyFormat:
    case OSCrypt::kDecryptionFailed:
      return false;
  }

  std::string key(kKeyLength, '\0');
  crypto::RandBytes(base::as_writable_byte_span(key));
  if (!EncryptAndStoreKey(key, local_state))
    return false;

  local_state->SetBoolean(kOsCryptAuditEnabledPrefName, true);
  encryption_key_.assign(key);
  return true;
}'''

SYNC_INIT_EXISTING = r'''OSCrypt::InitResult OSCryptImpl::InitWithExistingKey(
    PrefService* local_state) {
  DCHECK(encryption_key_.empty()) << "Key already exists.";
  if (!local_state->HasPrefPath(kOsCryptEncryptedKeyPrefName))
    return OSCrypt::kKeyDoesNotExist;

  const std::string base64_encrypted_key =
      local_state->GetString(kOsCryptEncryptedKeyPrefName);
  std::string encrypted_key_with_header;
  if (!base::Base64Decode(base64_encrypted_key, &encrypted_key_with_header))
    return OSCrypt::kInvalidKeyFormat;

  std::string key;
  if (base::StartsWith(encrypted_key_with_header, kPortableKeyPrefix,
                       base::CompareCase::SENSITIVE)) {
    if (!DecryptStringWithPortableKey(encrypted_key_with_header, &key))
      return OSCrypt::kDecryptionFailed;
  } else {
    if (!base::StartsWith(encrypted_key_with_header, kDPAPIKeyPrefix,
                          base::CompareCase::SENSITIVE)) {
      LOG(ERROR) << "Unknown OSCrypt key header.";
      return OSCrypt::kInvalidKeyFormat;
    }

    const std::string encrypted_key =
        encrypted_key_with_header.substr(sizeof(kDPAPIKeyPrefix) - 1);

    // Older ungoogled builds with --disable-encryption stored the raw 32-byte
    // profile AES key after the legacy DPAPI marker.
    if (encrypted_key.size() == kKeyLength) {
      key = encrypted_key;
    } else if (!DecryptStringWithDPAPI(encrypted_key, &key)) {
      base::UmaHistogramSparse("OSCrypt.Win.KeyDecryptionError",
                               ::GetLastError());
      return OSCrypt::kDecryptionFailed;
    }

    if (key.size() != kKeyLength)
      return OSCrypt::kInvalidKeyFormat;

    // Migrate the old key only after it has been successfully recovered.
    if (!EncryptAndStoreKey(key, local_state)) {
      LOG(ERROR) << "Failed to migrate the OSCrypt profile key to portable form.";
      return OSCrypt::kDecryptionFailed;
    }
  }

  if (key.size() != kKeyLength)
    return OSCrypt::kInvalidKeyFormat;

  local_state->SetBoolean(kOsCryptAuditEnabledPrefName, true);
  encryption_key_.assign(key);
  return OSCrypt::kSuccess;
}'''

ASYNC_PREFIX = '''constexpr uint8_t kPortableKeyPrefix[] = {'P', 'O', 'R', 'T', 'A', 'B', 'L', 'E'};
constexpr char kPortableKeyAad[] = "ungoogled-chromium-portable-oscrypt-key-v1";
constexpr unsigned char kPortableOsCryptWrappingKey[] = {
    {key_values}
};
static_assert(sizeof(kPortableOsCryptWrappingKey) == 32,
              "Portable OSCrypt wrapping key must be 256 bits.");
'''

ASYNC_HELPER = r'''std::optional<std::vector<uint8_t>> DecryptKeyWithPortableKey(
    base::span<const uint8_t> ciphertext) {
  constexpr size_t kNonceLength = 12;
  constexpr size_t kAeadTagLength = 16;
  constexpr size_t kPrefixLength = std::size(kPortableKeyPrefix);
  if (ciphertext.size() < kPrefixLength + kNonceLength + kAeadTagLength)
    return std::nullopt;

  if (!std::equal(ciphertext.begin(), ciphertext.begin() + kPrefixLength,
                  kPortableKeyPrefix)) {
    return std::nullopt;
  }

  const std::string wrapping_key(
      reinterpret_cast<const char*>(kPortableOsCryptWrappingKey),
      sizeof(kPortableOsCryptWrappingKey));
  crypto::Aead aead(crypto::Aead::AES_256_GCM);
  aead.Init(&wrapping_key);

  const std::string nonce(
      reinterpret_cast<const char*>(ciphertext.data() + kPrefixLength),
      kNonceLength);
  const std::string encrypted_data(
      reinterpret_cast<const char*>(ciphertext.data() + kPrefixLength + kNonceLength),
      ciphertext.size() - kPrefixLength - kNonceLength);
  std::string plaintext;
  if (!aead.Open(encrypted_data, nonce, kPortableKeyAad, &plaintext))
    return std::nullopt;

  return std::vector<uint8_t>(plaintext.begin(), plaintext.end());
}
'''

ASYNC_GET_KEY = r'''base::expected<Encryptor::Key, DPAPIKeyProvider::KeyStatus>
DPAPIKeyProvider::GetKeyInternal() {
  if (!local_state_->HasPrefPath(kOsCryptEncryptedKeyPrefName))
    return base::unexpected(KeyStatus::kKeyNotFound);

  const std::string base64_encrypted_key =
      local_state_->GetString(kOsCryptEncryptedKeyPrefName);
  std::optional<std::vector<uint8_t>> decoded =
      base::Base64Decode(base64_encrypted_key);
  if (!decoded)
    return base::unexpected(KeyStatus::kKeyDecodeFailure);

  std::optional<std::vector<uint8_t>> decrypted_key;
  if (decoded->size() >= std::size(kPortableKeyPrefix) &&
      std::equal(decoded->begin(),
                 decoded->begin() + std::size(kPortableKeyPrefix),
                 kPortableKeyPrefix)) {
    decrypted_key = DecryptKeyWithPortableKey(*decoded);
  } else {
    if (decoded->size() < std::size(kDPAPIKeyPrefix))
      return base::unexpected(KeyStatus::kKeyTooShort);

    if (!std::equal(decoded->begin(),
                    decoded->begin() + std::size(kDPAPIKeyPrefix),
                    kDPAPIKeyPrefix)) {
      return base::unexpected(KeyStatus::kInvalidKeyHeader);
    }

    auto encrypted_key_data = std::vector<uint8_t>(
        decoded->cbegin() + std::size(kDPAPIKeyPrefix), decoded->cend());

    // Old portable profiles created with --disable-encryption stored the raw
    // 32-byte AES key after the DPAPI marker instead of a DPAPI blob.
    if (encrypted_key_data.size() == Encryptor::Key::kAES256GCMKeySize) {
      decrypted_key = std::move(encrypted_key_data);
    } else {
      decrypted_key = DecryptKeyWithDPAPI(encrypted_key_data);
    }
  }

  if (!decrypted_key)
    return base::unexpected(KeyStatus::kDPAPIDecryptFailure);

  if (decrypted_key->size() != Encryptor::Key::kAES256GCMKeySize)
    return base::unexpected(KeyStatus::kInvalidKeyLength);

  return Encryptor::Key(*decrypted_key, mojom::Algorithm::kAES256GCM);
}'''


def fail(message: str) -> None:
    raise RuntimeError(message)


def replace_function(source: str, signature: str, replacement: str, label: str) -> str:
    matches = source.count(signature)
    if matches != 1:
        fail(f"Expected exactly one function signature for {label}; found {matches}.")
    start = source.find(signature)
    brace = source.find("{", start + len(signature))
    if brace < 0:
        fail(f"Could not find function body: {label}")

    depth = 0
    state = "normal"
    i = brace
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if state == "normal":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                i += 2
                continue
            if ch == '"':
                state = "string"
                i += 1
                continue
            if ch == "'":
                state = "char"
                i += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return source[:start] + replacement + source[i + 1:]
        elif state == "line_comment":
            if ch == "\n":
                state = "normal"
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "normal"
                i += 2
                continue
        elif state == "string":
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                state = "normal"
        elif state == "char":
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                state = "normal"
        i += 1
    fail(f"Unterminated function body: {label}")
    return source


def read_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    return raw.decode("utf-8").replace("\r\n", "\n"), newline


def write_text(path: Path, text: str, newline: str) -> None:
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        fail(f"Expected exactly one anchor for {label}; found {count}.")
    return source.replace(old, new, 1)


def patch_sync(path: Path, key_values: str) -> None:
    source, newline = read_text(path)
    prefix = SYNC_PREFIX.replace("{key_values}", key_values)
    source = replace_once(
        source,
        'constexpr char kDPAPIKeyPrefix[] = "DPAPI";\n',
        'constexpr char kDPAPIKeyPrefix[] = "DPAPI";\n' + prefix,
        "sync portable key constants",
    )
    source = replace_once(
        source,
        "bool EncryptAndStoreKey(const std::string& key, PrefService* local_state) {",
        SYNC_HELPERS + "\n" + "bool EncryptAndStoreKey(const std::string& key, PrefService* local_state) {",
        "sync portable encryption helpers",
    )
    source = replace_function(
        source,
        "bool EncryptAndStoreKey(const std::string& key, PrefService* local_state)",
        SYNC_ENCRYPT_AND_STORE,
        "EncryptAndStoreKey",
    )
    source = replace_function(
        source,
        "bool OSCryptImpl::Init(PrefService* local_state)",
        SYNC_INIT,
        "OSCryptImpl::Init",
    )
    source = replace_function(
        source,
        "OSCrypt::InitResult OSCryptImpl::InitWithExistingKey(PrefService* local_state)",
        SYNC_INIT_EXISTING,
        "OSCryptImpl::InitWithExistingKey",
    )
    write_text(path, source, newline)


def patch_async(path: Path, key_values: str) -> None:
    source, newline = read_text(path)
    if '#include "crypto/aead.h"' not in source:
        source = replace_once(
            source,
            '#include "base/win/scoped_localalloc.h"\n',
            '#include "base/win/scoped_localalloc.h"\n#include "crypto/aead.h"\n',
            "async crypto/aead.h include",
        )
    prefix_anchor = "constexpr uint8_t kDPAPIKeyPrefix[] = {'D', 'P', 'A', 'P', 'I'};\n"
    source = replace_once(
        source,
        prefix_anchor,
        prefix_anchor + ASYNC_PREFIX.replace("{key_values}", key_values),
        "async portable key constants",
    )
    source = replace_once(
        source,
        "std::optional<std::vector<uint8_t>> DecryptKeyWithDPAPI(\n",
        ASYNC_HELPER + "\n" + "std::optional<std::vector<uint8_t>> DecryptKeyWithDPAPI(\n",
        "async portable decryption helper",
    )
    source = replace_function(
        source,
        "base::expected<Encryptor::Key, DPAPIKeyProvider::KeyStatus>\nDPAPIKeyProvider::GetKeyInternal()",
        ASYNC_GET_KEY,
        "DPAPIKeyProvider::GetKeyInternal",
    )
    write_text(path, source, newline)


def patch_async_build(path: Path) -> None:
    source, newline = read_text(path)
    target = 'source_set("dpapi_key_provider") {'
    start = source.find(target)
    if start < 0:
        fail("Could not find source_set(\"dpapi_key_provider\") in async BUILD.gn.")
    deps_start = source.find("deps = [", start)
    if deps_start < 0:
        fail("Could not find dpapi_key_provider deps list in async BUILD.gn.")
    deps_end = source.find("]", deps_start)
    if deps_end < 0:
        fail("Could not find end of dpapi_key_provider deps list in async BUILD.gn.")
    deps_block = source[deps_start:deps_end]
    if '"//crypto"' not in deps_block:
        source = source[:deps_end] + '      "//crypto",\n' + source[deps_end:]
    write_text(path, source, newline)


def patch_chrome_startup(path: Path) -> None:
    source, newline = read_text(path)
    old = "DCHECK(os_crypt_init);"
    new = 'CHECK(os_crypt_init) << "Failed to initialize portable OSCrypt.";'
    if old in source:
        source = replace_once(source, old, new, "release OSCrypt init check")
    elif "CHECK(os_crypt_init)" not in source:
        fail("Could not find OSCrypt initialization check in chrome_browser_main_win.cc.")
    write_text(path, source, newline)


def main() -> int:
    key_hex = os.environ.get(KEY_ENV, "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", key_hex):
        print(
            "PORTABLE_OSCRYPT_KEY is missing or invalid; expected exactly 64 hexadecimal characters.",
            file=sys.stderr,
        )
        return 2
    if len(sys.argv) != 2:
        print("Usage: apply_portable_oscrypt.py <chromium-source-tree>", file=sys.stderr)
        return 2

    source_tree = Path(sys.argv[1]).resolve()
    key_bytes = bytes.fromhex(key_hex)
    key_values = ", ".join(f"0x{value:02X}" for value in key_bytes)

    files = [source_tree / rel for rel in (SYNC_REL, ASYNC_REL, ASYNC_BUILD_REL, CHROME_MAIN_REL)]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        print("Portable OSCrypt source files are missing:\n" + "\n".join(missing), file=sys.stderr)
        return 2

    try:
        patch_sync(files[0], key_values)
        patch_async(files[1], key_values)
        patch_async_build(files[2])
        patch_chrome_startup(files[3])
    except Exception as exc:
        # Never include the key or key bytes in an error message.
        print(f"Could not apply portable OSCrypt source transformation: {exc}", file=sys.stderr)
        return 2

    print("Applied portable OSCrypt source transformations to sync and async providers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
