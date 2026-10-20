const core = require('@actions/core');
const exec = require('@actions/exec');
const { DefaultArtifactClient } = require('@actions/artifact');
const fs = require('fs').promises;
const path = require('path');

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
        // It is normal for the artifact not to exist on the first attempt.
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

async function run() {
  const finished = core.getBooleanInput('finished', { required: true });
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

  console.log(`finished=${finished}, from_artifact=${fromArtifact}, arch=${arch}`);

  // If an earlier stage completed the build, preserve its output and stop here.
  if (finished) {
    core.setOutput('finished', 'true');
    return;
  }

  const artifact = new DefaultArtifactClient();

  if (fromArtifact) {
    const info = await artifact.getArtifact(intermediateArtifactName);
    await artifact.downloadArtifact(info.artifact.id, { path: BUILD });

    if (!(await exists(DOWNLOADED_ARCHIVE))) {
      throw new Error(`Downloaded intermediate archive not found: ${DOWNLOADED_ARCHIVE}`);
    }

    // The intermediate archive is created with entries relative to BUILD (src/...).
    // Extracting to BUILD must recreate BUILD/src, not a nested repository path.
    await exec.exec('7z', ['x', DOWNLOADED_ARCHIVE, `-o${BUILD}`, '-y'], { cwd: ROOT });
    await fs.rm(DOWNLOADED_ARCHIVE, { force: true });

    if (!(await exists(path.join(SRC, 'BUILD.gn')))) {
      throw new Error(
        `Restored source tree is missing ${path.join(SRC, 'BUILD.gn')}. ` +
        'The intermediate ZIP must contain src/BUILD.gn at its root.'
      );
    }
    console.log('Restored source tree successfully.');
  }

  const args = ['build.py', '--ci'];
  if (x86) args.push('--x86');
  if (arm) args.push('--arm');

  await exec.exec('python', ['-m', 'pip', 'install', 'httplib2==0.22.0'], {
    cwd: ROOT,
    ignoreReturnCode: true,
  });

  const exitCode = await exec.exec('python', args, {
    cwd: ROOT,
    ignoreReturnCode: true,
  });
  console.log(`build.py exit code: ${exitCode}`);

  if (exitCode === 0) {
    // package.py emits the actual unpacked-browser ZIP in BUILD.
    const packagePattern = path.join(BUILD, `ungoogled-chromium_*_windows_${arch}.zip`);
    const glob = require('@actions/glob');
    const packageGlob = await glob.create(packagePattern, { matchDirectories: false });
    const packageZips = await packageGlob.glob();
    if (packageZips.length === 0) {
      throw new Error(
        `Build returned success, but package.py ZIP was not found: ${packagePattern}. ` +
        'The workflow must publish the browser package, not mini_installer.exe.'
      );
    }
    if (packageZips.length > 1) {
      throw new Error(`Expected one browser package ZIP, found ${packageZips.length}: ${packageZips.join(', ')}`);
    }

    await fs.rm(PORTABLE_DIR, { recursive: true, force: true });
    await fs.mkdir(PORTABLE_DIR, { recursive: true });
    await exec.exec('7z', ['x', packageZips[0], `-o${PORTABLE_DIR}`, '-y'], { cwd: BUILD });

    const chromeExe = path.join(PORTABLE_DIR, 'chrome.exe');
    if (!(await exists(chromeExe))) {
      throw new Error(
        `The package ZIP was extracted, but chrome.exe is missing from ${PORTABLE_DIR}. ` +
        'Inspect package.py ZIP entries before publishing.'
      );
    }

    // Marker consumed by the portable-profile patch; no browser command-line switch is used.
    await fs.writeFile(path.join(PORTABLE_DIR, 'portable.flag'), '');
    await fs.writeFile(
      path.join(PORTABLE_DIR, 'Launch.bat'),
      '@echo off\r\ncd /d "%~dp0"\r\nstart "" "%~dp0chrome.exe"\r\n',
      'ascii'
    );

    const portableFiles = await collectFilesRecursively(PORTABLE_DIR);
    if (portableFiles.length === 0) {
      throw new Error(`Portable directory is empty: ${PORTABLE_DIR}`);
    }

    // Keep chromePortable/ as the top-level directory when the Actions artifact is extracted.
    // A release job can then create the downloadable chromePortable.zip from that folder.
    await uploadWithRetry(artifact, finalArtifactName, portableFiles, BUILD, 6);
    core.setOutput('finished', 'true');
    return;
  }

  // The CI build deliberately stops after its time budget; save src for the next stage.
  if (!(await exists(path.join(SRC, 'BUILD.gn')))) {
    throw new Error(
      `build.py returned ${exitCode}, but ${path.join(SRC, 'BUILD.gn')} is missing. ` +
      'This looks like a source preparation/patch error rather than a resumable compile stage.'
    );
  }

  await fs.rm(STAGE_ARCHIVE, { force: true });
  // Use relative entries (src/...) so the next runner can extract into BUILD correctly.
  await exec.exec('7z', ['a', '-tzip', STAGE_ARCHIVE, 'src', '-mx=3', '-mtc=on'], {
    cwd: BUILD,
  });

  const archiveInfo = await fs.stat(STAGE_ARCHIVE);
  if (archiveInfo.size === 0) {
    throw new Error('The intermediate source archive is empty.');
  }

  await uploadWithRetry(artifact, intermediateArtifactName, [STAGE_ARCHIVE], ROOT, 0);
  core.setOutput('finished', 'false');
}

run().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  core.setFailed(error && error.message ? error.message : String(error));
});
