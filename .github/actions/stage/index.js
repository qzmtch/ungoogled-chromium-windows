'use strict';

const core = require('@actions/core');
const exec = require('@actions/exec');
const { DefaultArtifactClient } = require('@actions/artifact');
const glob = require('@actions/glob');
const fs = require('fs').promises;
const path = require('path');
const {
  collectPortableOwnedSources,
  findPortableOwnedSourceErrors,
} = require('./failure_classifier');

const ROOT = 'C:\\ungoogled-chromium-windows';
const BUILD = path.join(ROOT, 'build');
const SRC = path.join(BUILD, 'src');
const PORTABLE_DIR = path.join(BUILD, 'chromePortable');
const STAGE_ARCHIVE = path.join(ROOT, 'artifacts.zip');
const DOWNLOADED_ARCHIVE = path.join(BUILD, 'artifacts.zip');

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function exists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function collectFilesRecursively(dir) {
  const result = [];
  const entries = await fs.readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      result.push(...await collectFilesRecursively(fullPath));
    } else if (entry.isFile()) {
      result.push(fullPath);
    }
  }
  return result;
}

async function uploadWithRetry(artifact, name, files, rootDirectory, compressionLevel) {
  let lastError;
  for (let attempt = 1; attempt <= 5; attempt++) {
    try {
      try {
        await artifact.deleteArtifact(name);
      } catch {
        // The artifact normally does not exist on the first attempt.
      }
      await artifact.uploadArtifact(name, files, rootDirectory, {
        retentionDays: 1,
        compressionLevel,
      });
      console.log(`Artifact uploaded: ${name}`);
      return;
    } catch (error) {
      lastError = error;
      console.error(`Upload failed for ${name} (${attempt}/5): ${error}`);
      if (attempt < 5) await delay(10000);
    }
  }
  throw new Error(`Could not upload artifact "${name}" after 5 attempts: ${lastError}`);
}

async function restoreIntermediateSource(artifact, artifactName) {
  let info;
  try {
    info = await artifact.getArtifact(artifactName);
  } catch (error) {
    throw new Error(`Could not find intermediate artifact "${artifactName}": ${error.message || error}`);
  }
  if (!info || !info.artifact || !info.artifact.id) {
    throw new Error(`Intermediate artifact "${artifactName}" was not found in this workflow run.`);
  }
  await fs.mkdir(BUILD, { recursive: true });
  await fs.rm(DOWNLOADED_ARCHIVE, { force: true });
  await artifact.downloadArtifact(info.artifact.id, { path: BUILD });

  if (!(await exists(DOWNLOADED_ARCHIVE))) {
    throw new Error(`Downloaded intermediate archive not found: ${DOWNLOADED_ARCHIVE}`);
  }
  // The archive contains relative entries such as src/BUILD.gn.
  await exec.exec('7z', ['x', DOWNLOADED_ARCHIVE, `-o${BUILD}`, '-y'], { cwd: ROOT });
  await fs.rm(DOWNLOADED_ARCHIVE, { force: true });

  if (!(await exists(path.join(SRC, 'BUILD.gn')))) {
    throw new Error(
      `Restored source tree is missing ${path.join(SRC, 'BUILD.gn')}. ` +
      'The intermediate archive must contain src/BUILD.gn at its root.'
    );
  }
  console.log('Restored source tree successfully.');
}

async function createIntermediateArtifact(artifact, artifactName) {
  // Save a resumable checkpoint after the CI time limit or a retryable Ninja
  // failure that does not point to a source file changed by this repository.
  const requiredForResume = [
    path.join(SRC, 'BUILD.gn'),
    path.join(SRC, 'out', 'Default', 'args.gn'),
    path.join(SRC, 'out', 'Default', 'gn.exe'),
    path.join(SRC, 'third_party', 'rust-toolchain', 'bin', 'bindgen.exe'),
    path.join(SRC, 'third_party', 'ninja', 'ninja.exe'),
  ];
  const missing = [];
  for (const filePath of requiredForResume) {
    if (!(await exists(filePath))) missing.push(filePath);
  }
  if (missing.length) {
    throw new Error(
      'build.py did not complete the source preparation/build-tool setup. ' +
      'Refusing to upload an incomplete intermediate artifact. Missing:\n' + missing.join('\n')
    );
  }
  await fs.rm(STAGE_ARCHIVE, { force: true });
  // Store relative paths so extracting into build recreates build/src.
  // 7-Zip may return code 1 for missing/broken non-Windows source paths while
  // still producing a usable ZIP. Accept only code 0 or 1, and test the ZIP
  // before uploading it. Fatal 7-Zip errors and a failed integrity test stop the stage.
  const archiveExitCode = await exec.exec(
    '7z',
    ['a', '-tzip', STAGE_ARCHIVE, 'src', '-mx=3', '-mtc=on'],
    {
      cwd: BUILD,
      ignoreReturnCode: true,
    }
  );
  if (archiveExitCode !== 0 && archiveExitCode !== 1) {
    throw new Error(`7-Zip failed to create the intermediate archive (exit code ${archiveExitCode}).`);
  }

  const archiveInfo = await fs.stat(STAGE_ARCHIVE).catch(() => null);
  if (!archiveInfo || !archiveInfo.size) {
    throw new Error('The intermediate source archive is missing or empty.');
  }
  if (archiveExitCode === 1) {
    core.warning(
      '7-Zip returned warning code 1 while creating the intermediate ZIP. ' +
      'Testing the archive before upload; review the missing-path warnings above.'
    );
  }
  const testExitCode = await exec.exec('7z', ['t', STAGE_ARCHIVE], {
    cwd: BUILD,
    ignoreReturnCode: true,
  });
  if (testExitCode !== 0) {
    throw new Error(
      `The intermediate source ZIP failed 7-Zip integrity testing (exit code ${testExitCode}); it will not be uploaded.`
    );
  }

  console.log(`Intermediate ZIP passed integrity test (${archiveInfo.size} bytes).`);
  await uploadWithRetry(artifact, artifactName, [STAGE_ARCHIVE], ROOT, 0);
  core.setOutput('finished', 'false');
}

async function moveChromeProxyToPortableRoot() {
  const proxyAtRoot = path.join(PORTABLE_DIR, 'chrome_proxy.exe');
  if (await exists(proxyAtRoot)) {
    return;
  }

  const entries = await fs.readdir(PORTABLE_DIR, { withFileTypes: true });
  const candidates = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const candidate = path.join(PORTABLE_DIR, entry.name, 'chrome_proxy.exe');
    if (await exists(candidate)) candidates.push(candidate);
  }

  if (candidates.length !== 1) {
    throw new Error(
      `Expected exactly one versioned chrome_proxy.exe to move beside chrome.exe, found ${candidates.length}. ` +
      'Check chrome/tools/build/win/FILES.cfg and the package.py contents.'
    );
  }

  await fs.rename(candidates[0], proxyAtRoot);
  console.log(`Moved chrome_proxy.exe to portable root: ${proxyAtRoot}`);
}

async function createPortableArtifact(artifact, finalArtifactName, arch) {
  // build.py --ci runs package.py itself after the full compile succeeds.
  const packagePattern = path.join(BUILD, `ungoogled-chromium_*_windows_${arch}.zip`);
  const packageGlob = await glob.create(packagePattern, { matchDirectories: false });
  const packageZips = await packageGlob.glob();
  if (packageZips.length === 0) {
    throw new Error(
      `build.py returned success, but the browser package ZIP was not found: ${packagePattern}. ` +
      'Refusing to publish the installer or an incomplete browser.'
    );
  }
  if (packageZips.length > 1) {
    throw new Error(`Expected one browser package ZIP, found ${packageZips.length}: ${packageZips.join(', ')}`);
  }

  await fs.rm(PORTABLE_DIR, { recursive: true, force: true });
  await fs.mkdir(PORTABLE_DIR, { recursive: true });
  await exec.exec('7z', ['x', packageZips[0], `-o${PORTABLE_DIR}`, '-y'], { cwd: BUILD });

  // Flatten legacy package archives that contain an extra wrapper directory.
  const packageRootName = path.basename(packageZips[0], path.extname(packageZips[0]));
  const extractedPackageRoot = path.join(PORTABLE_DIR, packageRootName);
  const chromeExeInPackageRoot = path.join(extractedPackageRoot, 'chrome.exe');
  const chromeExeAtRoot = path.join(PORTABLE_DIR, 'chrome.exe');
  if (!(await exists(chromeExeAtRoot)) && await exists(chromeExeInPackageRoot)) {
    console.log(`Flattening package archive root: ${extractedPackageRoot}`);
    const rootEntries = await fs.readdir(extractedPackageRoot);
    for (const entryName of rootEntries) {
      const sourcePath = path.join(extractedPackageRoot, entryName);
      const destinationPath = path.join(PORTABLE_DIR, entryName);
      if (await exists(destinationPath)) {
        throw new Error(`Cannot flatten browser package: destination already exists: ${destinationPath}`);
      }
      await fs.rename(sourcePath, destinationPath);
    }
    await fs.rm(extractedPackageRoot, { recursive: true, force: true });
  }

  const chromeExe = path.join(PORTABLE_DIR, 'chrome.exe');
  if (!(await exists(chromeExe))) {
    throw new Error(
      `Browser ZIP extracted, but chrome.exe is missing from ${PORTABLE_DIR}. ` +
      'Inspect package.py ZIP entries before publishing.'
    );
  }

  // Portable source layout selects UserData beside this root chrome.exe directly;
  // no marker file is needed in the final package.
  await fs.rm(path.join(PORTABLE_DIR, 'portable.flag'), { force: true });

  // chrome_proxy.exe must share a directory with chrome.exe because the proxy
  // resolves the browser executable relative to its own directory. Keep
  // chrome_pwa_launcher.exe and the remaining versioned payload in the version folder.
  await moveChromeProxyToPortableRoot();

  let hasChromeDll = await exists(path.join(PORTABLE_DIR, 'chrome.dll'));
  if (!hasChromeDll) {
    const entries = await fs.readdir(PORTABLE_DIR, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory() && await exists(path.join(PORTABLE_DIR, entry.name, 'chrome.dll'))) {
        hasChromeDll = true;
        break;
      }
    }
  }
  if (!hasChromeDll) {
    throw new Error(`chrome.dll was not found next to chrome.exe or in an immediate subfolder of ${PORTABLE_DIR}.`);
  }

  const portableFiles = await collectFilesRecursively(PORTABLE_DIR);
  if (!portableFiles.length) {
    throw new Error(`Portable directory is empty: ${PORTABLE_DIR}`);
  }

  // Upload individual files with chromePortable/ as their preserved top-level path.
  // The release job can then zip only the compiled browser, not build/src.
  await uploadWithRetry(artifact, finalArtifactName, portableFiles, BUILD, 6);
  core.setOutput('finished', 'true');
}

async function run() {
  console.log('[PORTABLE-LAYOUT-FIX-v3] chrome.exe and chrome_proxy.exe are staged at portable root.');
  console.log('[RESUMABLE-NINJA-CHECKPOINT-v1] Ninja failure recovery handler loaded.');
  console.log('[STAGE-INPUT-FIX-v1] Missing finished input safely defaults to false.');
  console.log('[PORTABLE-PATCH-FAIL-FAST-v1] Owned-source compiler errors stop the stage chain.');

  const finishedInput = core.getInput('finished').trim();
  const finished = finishedInput === '' ? false : core.getBooleanInput('finished');
  const fromArtifact = core.getBooleanInput('from_artifact', { required: true });
  const x86 = core.getBooleanInput('x86', { required: false });
  const arm = core.getBooleanInput('arm', { required: false });
  const intermediateArtifactName = x86
    ? 'build-artifact-x86'
    : (arm ? 'build-artifact-arm' : 'build-artifact');
  const finalArtifactName = x86
    ? 'chromium-x86'
    : (arm ? 'chromium-arm' : 'chromium');
  const arch = x86 ? 'x86' : (arm ? 'arm64' : 'x64');

  console.log(`finished=${finished}, fromArtifact=${fromArtifact}, arch=${arch}`);
  if (finished) {
    core.setOutput('finished', 'true');
    return;
  }

  const artifact = new DefaultArtifactClient();
  if (fromArtifact) {
    await restoreIntermediateSource(artifact, intermediateArtifactName);
  }

  const args = ['build.py', '--ci'];
  if (x86) args.push('--x86');
  if (arm) args.push('--arm');
  await exec.exec('python', ['-m', 'pip', 'install', 'httplib2==0.22.0'], {
    cwd: ROOT,
    ignoreReturnCode: true,
  });

  // Capture the end of build.py output to distinguish the CI timeout, errors in
  // source files changed by our portable patches, and unrelated/upstream Ninja
  // failures. Only the last category is checkpointed and retried by the next stage.
  const ownership = await collectPortableOwnedSources(ROOT);
  const ownedPortableSources = ownership.ownedSources;
  console.log(`[PORTABLE-PATCH-FAIL-FAST-v1] Tracking ${ownedPortableSources.size} source file(s) from post-processors and active Windows patches.`);
  if (!ownership.seriesPath) {
    core.warning('Could not locate the Windows patch series file; fail-fast still tracks the portable source post-processors.');
  }
  let recentBuildOutput = '';
  const rememberBuildOutput = (chunk) => {
    recentBuildOutput = (recentBuildOutput + chunk.toString()).slice(-200000);
  };

  const exitCode = await exec.exec('python', args, {
    cwd: ROOT,
    ignoreReturnCode: true,
    listeners: {
      stdout: rememberBuildOutput,
      stderr: rememberBuildOutput,
    },
  });
  console.log(`build.py exit code: ${exitCode}`);
  if (exitCode === 0) {
    await createPortableArtifact(artifact, finalArtifactName, arch);
    return;
  }

  const expectedCiTimeout = /Sending keyboard interrupt/i.test(recentBuildOutput);
  const ninjaSubcommandFailure =
    /ninja:\s*build stopped:\s*subcommand failed/i.test(recentBuildOutput) &&
    /RuntimeError:\s*Build failed!/i.test(recentBuildOutput);
  if (!expectedCiTimeout && !ninjaSubcommandFailure) {
    throw new Error(
      `build.py failed with exit code ${exitCode}. The log does not identify this as ` +
      'a resumable Ninja failure or the expected CI time-limit stop; no intermediate ' +
      'artifact will be created. See the build.py error above.'
    );
  }

  if (ninjaSubcommandFailure) {
    const ownedSourceErrors = findPortableOwnedSourceErrors(
      recentBuildOutput,
      ownedPortableSources
    );
    if (ownedSourceErrors.length) {
      console.error('[PORTABLE-PATCH-FAIL-FAST-v1] Compiler error detected in source modified by this repository:');
      for (const item of ownedSourceErrors) {
        core.error(`${item.sourcePath}: ${item.diagnostic}`);
      }
      throw new Error(
        `Ninja failed while compiling ${ownedSourceErrors.length} source file(s) changed by the portable-browser post-processors or Windows patches. ` +
        'Stopping the remaining build stages now; no intermediate checkpoint will be uploaded. ' +
        'Fix these source errors and restart the workflow from build-1.'
      );
    }
    core.warning(
      'Ninja failed, but the compiler diagnostics do not point to files changed by this repository. ' +
      'Keeping the existing incremental checkpoint/retry behavior for upstream or unrelated failures.'
    );
    console.log('[RESUMABLE-NINJA-CHECKPOINT-v1] No portable-patch source error found; saving checkpoint for the next stage.');
  } else {
    console.log('Detected build.py CI time-limit stop; validating build state before saving intermediate source.');
  }
  await createIntermediateArtifact(artifact, intermediateArtifactName);
}

run().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  core.setFailed(error && error.message ? error.message : String(error));
});
