'use strict';

const fs = require('fs').promises;
const path = require('path');

const SOURCE_EXTENSIONS = /\.(?:c|cc|cpp|cxx|h|hh|hpp|mm|m)$/i;
const SOURCE_LITERAL_PATTERN = /["']((?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+\.(?:c|cc|cpp|cxx|h|hh|hpp|mm|m))["']/gi;
const PATCH_FILE_HEADER_PATTERN = /^\+\+\+\s+([^\s]+)/gm;
const COMPILER_DIAGNOSTIC_PATTERN = /([A-Za-z0-9_.:+/\\-]+\.(?:c|cc|cpp|cxx|h|hh|hpp|mm|m))(?:(?:\(\d+(?:,\d+)?\))|(?::\d+(?::\d+)?)):\s*(?:fatal\s+)?error:/gi;

const POSTPROCESSOR_FILES = [
  'apply_portable_oscrypt.py',
  'apply_portable_device_id.py',
  'apply_portable_ui_features.py',
  'fix_portable_ui_compiler.py',
];

// Keep a small fallback for the four C++ files that have already received
// explicit Chromium-141 compatibility edits. The manifest is normally built
// from the post-processors and the active Windows patch series below.
const KNOWN_PORTABLE_SOURCES = [
  'chrome/browser/ui/views/bookmarks/bookmark_bar_view.cc',
  'chrome/browser/ui/views/frame/browser_view.cc',
  'chrome/browser/ui/views/frame/browser_view.h',
  'chrome/browser/ui/bookmarks/bookmark_context_menu_controller.cc',
];

function normalizeSourcePath(value) {
  let normalized = String(value).replace(/\\/g, '/');
  const lower = normalized.toLowerCase();
  const sourceRootMarker = '/build/src/';
  const sourceRootIndex = lower.lastIndexOf(sourceRootMarker);
  if (sourceRootIndex >= 0) {
    normalized = normalized.slice(sourceRootIndex + sourceRootMarker.length);
  }

  normalized = normalized.replace(/^[a-z]:/i, '').replace(/^\/+/, '');
  while (normalized.startsWith('../')) normalized = normalized.slice(3);
  while (normalized.startsWith('./')) normalized = normalized.slice(2);
  if (normalized.toLowerCase().startsWith('src/')) normalized = normalized.slice(4);
  return normalized.toLowerCase();
}

async function readOptional(filePath) {
  try {
    return await fs.readFile(filePath, 'utf8');
  } catch (error) {
    if (error && error.code === 'ENOENT') return null;
    throw error;
  }
}

function addSourceLiterals(contents, ownedSources) {
  SOURCE_LITERAL_PATTERN.lastIndex = 0;
  for (const match of contents.matchAll(SOURCE_LITERAL_PATTERN)) {
    const sourcePath = normalizeSourcePath(match[1]);
    if (SOURCE_EXTENSIONS.test(sourcePath)) ownedSources.add(sourcePath);
  }
}

function addPatchHeaders(contents, ownedSources) {
  PATCH_FILE_HEADER_PATTERN.lastIndex = 0;
  for (const match of contents.matchAll(PATCH_FILE_HEADER_PATTERN)) {
    let sourcePath = match[1].replace(/\\/g, '/');
    if (sourcePath === '/dev/null') continue;
    sourcePath = sourcePath.replace(/^[ab]\//, '');
    sourcePath = normalizeSourcePath(sourcePath);
    if (SOURCE_EXTENSIONS.test(sourcePath)) ownedSources.add(sourcePath);
  }
}

async function resolvePatchFile(repositoryRoot, seriesPath, entry) {
  const cleanEntry = entry.replace(/^['"]|['"]$/g, '').replace(/\\/g, '/');
  const basename = path.basename(cleanEntry);
  const candidates = [
    path.resolve(path.dirname(seriesPath), cleanEntry),
    path.resolve(repositoryRoot, 'patches', 'ungoogled-chromium', 'windows', cleanEntry),
    path.resolve(repositoryRoot, 'patches', 'ungoogled-chromium', 'windows', basename),
    path.resolve(repositoryRoot, 'patches', 'ungoogled-chromium', cleanEntry),
    path.resolve(repositoryRoot, 'patches', cleanEntry),
  ];
  for (const candidate of candidates) {
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      // Try the next known patch-series layout.
    }
  }
  return null;
}

async function collectPortableOwnedSources(repositoryRoot) {
  const ownedSources = new Set();

  // The source transformers are the authoritative manifest for code changed
  // after the upstream and Windows patch stacks have been applied.
  for (const fileName of POSTPROCESSOR_FILES) {
    const contents = await readOptional(path.join(repositoryRoot, fileName));
    if (contents) addSourceLiterals(contents, ownedSources);
  }

  // Also include files touched by the active Windows patch series. These are
  // our repository's patches, not the upstream ungoogled-chromium patch set.
  const seriesCandidates = [
    path.join(repositoryRoot, 'patches', 'series'),
    path.join(repositoryRoot, 'patches', 'ungoogled-chromium', 'windows', 'series'),
    path.join(repositoryRoot, 'patches', 'ungoogled-chromium', 'series'),
  ];
  let seriesPath = null;
  let seriesContents = null;
  for (const candidate of seriesCandidates) {
    const contents = await readOptional(candidate);
    if (contents !== null) {
      seriesPath = candidate;
      seriesContents = contents;
      break;
    }
  }

  let patchFilesRead = 0;
  if (seriesPath && seriesContents) {
    for (const line of seriesContents.split(/\r?\n/)) {
      const entry = line.trim().split(/\s+/)[0];
      if (!entry || entry.startsWith('#') || !entry.toLowerCase().endsWith('.patch')) continue;
      const patchPath = await resolvePatchFile(repositoryRoot, seriesPath, entry);
      if (!patchPath) continue;
      const patchContents = await readOptional(patchPath);
      if (patchContents !== null) {
        addPatchHeaders(patchContents, ownedSources);
        patchFilesRead += 1;
      }
    }
  }

  for (const sourcePath of KNOWN_PORTABLE_SOURCES) {
    ownedSources.add(normalizeSourcePath(sourcePath));
  }

  return { ownedSources, patchFilesRead, seriesPath };
}

function findPortableOwnedSourceErrors(output, ownedSources) {
  const found = new Map();
  COMPILER_DIAGNOSTIC_PATTERN.lastIndex = 0;
  const lines = String(output).split(/\r?\n/);

  for (const line of lines) {
    COMPILER_DIAGNOSTIC_PATTERN.lastIndex = 0;
    for (const match of line.matchAll(COMPILER_DIAGNOSTIC_PATTERN)) {
      const sourcePath = normalizeSourcePath(match[1]);
      if (!ownedSources.has(sourcePath)) continue;
      if (!found.has(sourcePath)) {
        found.set(sourcePath, line.trim().slice(-1200));
      }
    }
  }

  return Array.from(found, ([sourcePath, diagnostic]) => ({ sourcePath, diagnostic }));
}

module.exports = {
  collectPortableOwnedSources,
  findPortableOwnedSourceErrors,
  normalizeSourcePath,
};
