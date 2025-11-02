const core = require('@actions/core');
const io = require('@actions/io');
const exec = require('@actions/exec');
const {DefaultArtifactClient} = require('@actions/artifact');
const glob = require('@actions/glob');
const fs = require('fs').promises;
const path = require('path');

// Files needed for Chrome to run (whitelist)
const REQUIRED_FILES = [
    'chrome.exe',
    'chrome.dll',
    'chrome_100_percent.pak',
    'chrome_200_percent.pak',
    'resources.pak',
    'chrome_elf.dll',
    'libEGL.dll',
    'libGLESv2.dll',
    'd3dcompiler_47.dll',
    'vk_swiftshader.dll',
    'vk_swiftshader_icd.json',
    'vulkan-1.dll',
    'icudtl.dat',
    'snapshot_blob.bin',
    'v8_context_snapshot.bin',
    'chrome_proxy.exe',
    'chrome_pwa_launcher.exe',
    'chrome_wer.dll',
    'chromedriver.exe',
    'dxcompiler.dll',
    'dxil.dll',
    'notification_helper.exe'
];

const REQUIRED_PATTERNS = [
    '*.pak',           // Resource files
    '*.bin',           // Binary data files
    '*.dat',           // Data files
];

const REQUIRED_DIRS = [
    'locales',         // Localization files
    'MEIPreload',      // Media Foundation
    'WidevineCdm',     // DRM (if exists)
    'IwaKeyDistribution'
];

const EXCLUDE_PATTERNS = [
    '.pdb',            // Debug symbols
    '.lib',            // Static libraries
    '.obj',            // Object files
    '.ilk',            // Incremental linker files
];

async function shouldIncludeFile(filename) {
    // Exclude patterns
    for (const pattern of EXCLUDE_PATTERNS) {
        if (filename.endsWith(pattern)) {
            return false;
        }
    }
    
    // Required files
    if (REQUIRED_FILES.includes(filename)) {
        return true;
    }
    
    // Required patterns
    for (const pattern of REQUIRED_PATTERNS) {
        if (pattern.startsWith('*.')) {
            const ext = pattern.substring(1);
            if (filename.endsWith(ext)) {
                return true;
            }
        }
    }
    
    return false;
}

async function createPortableStructure(buildDir, x86, arm) {
    console.log('Creating portable structure from build output...');
    
    const arch = x86 ? 'x86' : (arm ? 'arm64' : 'x64');
    const portableRoot = path.join(buildDir, `chromePortable-${arch}`);
    
    // Get version
    let version = '';
    try {
        const chromeExe = path.join(buildDir, 'chrome.exe');
        const {stdout} = await exec.getExecOutput('powershell', [
            '-Command',
            `(Get-Item "${chromeExe}").VersionInfo.FileVersion`
        ]);
        version = stdout.trim();
    } catch (e) {
        console.error('Failed to get version:', e);
        version = 'unknown';
    }
    
    console.log(`Version: ${version}`);
    
    const versionDir = path.join(portableRoot, version);
    const userDataDir = path.join(portableRoot, 'UserData');
    
    // Create directories
    await io.mkdirP(versionDir);
    await io.mkdirP(userDataDir);
    
    // Copy required files
    console.log('Copying required files...');
    const files = await fs.readdir(buildDir);
    
    let copiedFiles = 0;
    let skippedFiles = 0;
    
    for (const file of files) {
        const srcPath = path.join(buildDir, file);
        const stat = await fs.stat(srcPath);
        
        if (stat.isDirectory()) {
            // Copy required directories
            if (REQUIRED_DIRS.includes(file)) {
                console.log(`  Copying directory: ${file}`);
                await io.cp(srcPath, path.join(versionDir, file), {recursive: true});
                copiedFiles++;
            } else {
                skippedFiles++;
            }
        } else {
            // Check if file should be included
            if (await shouldIncludeFile(file)) {
                if (file === 'chrome.exe') {
                    // Copy chrome.exe to root
                    await io.cp(srcPath, path.join(portableRoot, 'chrome.exe'));
                    copiedFiles++;
                } else {
                    // Copy to version folder
                    await io.cp(srcPath, path.join(versionDir, file));
                    copiedFiles++;
                }
            } else {
                skippedFiles++;
            }
        }
    }
    
    console.log(`Copied ${copiedFiles} files/directories, skipped ${skippedFiles}`);
    
    // Create README
    const readme = `Ungoogled Chromium Portable ${version} (${arch})

=======================================================
PORTABLE VERSION - NO INSTALLATION REQUIRED
=======================================================

STRUCTURE:
----------
chromePortable-${arch}/
  ├── chrome.exe          ← Run this file
  ├── ${version}/         ← Browser binaries
  │   ├── chrome.dll
  │   ├── locales/
  │   ├── *.pak
  │   └── ...
  └── UserData/           ← Your profile (created on first run)

FEATURES:
---------
✅ Fully Portable       - Copy folder anywhere
✅ Symmetric Encryption - Cookies/passwords work on any PC
✅ Auto User Data Path  - No --user-data-dir needed  
✅ No Machine Binding   - No --disable-machine-id needed
✅ No DPAPI Dependency  - Transfer between Windows PCs freely

USAGE:
------
1. Extract this archive
2. Run chrome.exe
3. Your data is saved to UserData folder
4. To move to another PC: copy entire folder

For more info: https://github.com/ungoogled-software/ungoogled-chromium-windows
`;
    await fs.writeFile(path.join(portableRoot, 'README.txt'), readme);
    
    // Create .gitkeep
    await fs.writeFile(path.join(userDataDir, '.gitkeep'), '');
    
    // Create archive
    const archiveName = `ungoogled-chromium-${version}-${arch}-portable.zip`;
    const archivePath = path.join(buildDir, archiveName);
    
    console.log(`Creating archive: ${archiveName}`);
    await exec.exec('7z', [
        'a', '-tzip', archivePath, portableRoot,
        '-mx=5',      // Normal compression (faster than -mx=9)
        '-mmt=on'     // Multi-threaded
    ]);
    
    // Get archive size
    const archiveStat = await fs.stat(archivePath);
    console.log(`✓ Archive created: ${(archiveStat.size / 1024 / 1024).toFixed(2)} MB`);
    
    // Clean up
    await io.rmRF(portableRoot);
    
    return archivePath;
}

async function run() {
    process.on('SIGINT', function() {
    })
    const finished = core.getBooleanInput('finished', {required: true});
    const from_artifact = core.getBooleanInput('from_artifact', {required: true});
    const x86 = core.getBooleanInput('x86', {required: false})
    const arm = core.getBooleanInput('arm', {required: false})
    console.log(`finished: ${finished}, artifact: ${from_artifact}`);
    if (finished) {
        core.setOutput('finished', true);
        return;
    }

    const artifact = new DefaultArtifactClient();
    const artifactName = x86 ? 'build-artifact-x86' : (arm ? 'build-artifact-arm' : 'build-artifact');

    if (from_artifact) {
        const artifactInfo = await artifact.getArtifact(artifactName);
        await artifact.downloadArtifact(artifactInfo.artifact.id, {path: 'C:\\ungoogled-chromium-windows\\build'});
        await exec.exec('7z', ['x', 'C:\\ungoogled-chromium-windows\\build\\artifacts.zip',
            '-oC:\\ungoogled-chromium-windows\\build', '-y']);
        await io.rmRF('C:\\ungoogled-chromium-windows\\build\\artifacts.zip');
    }

    const args = ['build.py', '--ci']
    if (x86)
        args.push('--x86')
    if (arm)
        args.push('--arm')
    await exec.exec('python', ['-m', 'pip', 'install', 'httplib2==0.22.0'], {
        cwd: 'C:\\ungoogled-chromium-windows',
        ignoreReturnCode: true
    });
    const retCode = await exec.exec('python', args, {
        cwd: 'C:\\ungoogled-chromium-windows',
        ignoreReturnCode: true
    });
    if (retCode === 0) {
        core.setOutput('finished', true);
        
        // Find build directory
        const possiblePaths = [
            'C:\\ungoogled-chromium-windows\\build\\src\\out\\Default',
            'C:\\ungoogled-chromium-windows\\build\\src\\out\\Release',
        ];
        
        let buildOutDir = null;
        for (const testPath of possiblePaths) {
            const chromeExe = path.join(testPath, 'chrome.exe');
            try {
                await fs.access(chromeExe);
                buildOutDir = testPath;
                console.log(`Found build directory: ${buildOutDir}`);
                break;
            } catch (e) {
                // Continue
            }
        }
        
        if (!buildOutDir) {
            const globber = await glob.create('C:\\ungoogled-chromium-windows\\build\\src\\out\\*\\chrome.exe');
            const files = await globber.glob();
            if (files.length > 0) {
                buildOutDir = path.dirname(files[0]);
                console.log(`Found build directory via glob: ${buildOutDir}`);
            }
        }
        
        if (!buildOutDir) {
            throw new Error('Could not find build output directory');
        }
        
        // Create portable archive
        const portableArchive = await createPortableStructure(buildOutDir, x86, arm);
        
        // Upload
        const finalArtifactName = x86 ? 'chromium-x86' : (arm ? 'chromium-arm' : 'chromium');
        for (let i = 0; i < 5; ++i) {
            try {
                await artifact.deleteArtifact(finalArtifactName);
            } catch (e) {
                // ignored
            }
            try {
                await artifact.uploadArtifact(finalArtifactName, [portableArchive],
                    path.dirname(portableArchive), {retentionDays: 1, compressionLevel: 0});
                console.log(`✓ Artifact uploaded: ${finalArtifactName}`);
                break;
            } catch (e) {
                console.error(`Upload failed (attempt ${i + 1}/5): ${e}`);
                await new Promise(r => setTimeout(r, 10000));
            }
        }
    } else {
        await new Promise(r => setTimeout(r, 5000));
        await exec.exec('7z', ['a', '-tzip', 'C:\\ungoogled-chromium-windows\\artifacts.zip',
            'C:\\ungoogled-chromium-windows\\build\\src', '-mx=3', '-mtc=on'], {ignoreReturnCode: true});
        for (let i = 0; i < 5; ++i) {
            try {
                await artifact.deleteArtifact(artifactName);
            } catch (e) {
                // ignored
            }
            try {
                await artifact.uploadArtifact(artifactName, ['C:\\ungoogled-chromium-windows\\artifacts.zip'],
                    'C:\\ungoogled-chromium-windows', {retentionDays: 1, compressionLevel: 0});
                break;
            } catch (e) {
                console.error(`Upload artifact failed: ${e}`);
                await new Promise(r => setTimeout(r, 10000));
            }
        }
        core.setOutput('finished', false);
    }
}

run().catch(err => core.setFailed(err.message));
