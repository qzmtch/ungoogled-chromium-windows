const core = require('@actions/core');
const io = require('@actions/io');
const exec = require('@actions/exec');
const {DefaultArtifactClient} = require('@actions/artifact');
const glob = require('@actions/glob');
const fs = require('fs').promises;
const path = require('path');

async function createPortableStructure(buildDir, x86, arm) {
    console.log('Creating portable structure...');
    
    const arch = x86 ? 'x86' : (arm ? 'arm64' : 'x64');
    const portableRoot = path.join(buildDir, `chromePortable-${arch}`);
    
    // Read version from version.dll or chrome.exe
    let version = '';
    try {
        const {stdout} = await exec.getExecOutput('powershell', [
            '-Command',
            `(Get-Item "${buildDir}\\chrome.exe").VersionInfo.FileVersion`
        ]);
        version = stdout.trim();
    } catch (e) {
        console.error('Failed to get version:', e);
        version = 'unknown';
    }
    
    console.log(`Detected version: ${version}`);
    
    const versionDir = path.join(portableRoot, version);
    const userDataDir = path.join(portableRoot, 'UserData');
    
    // Create directories
    await io.mkdirP(versionDir);
    await io.mkdirP(userDataDir);
    
    // Get list of files in build directory
    const files = await fs.readdir(buildDir);
    
    // Copy files
    for (const file of files) {
        const srcPath = path.join(buildDir, file);
        const stat = await fs.stat(srcPath);
        
        if (file === 'chrome.exe') {
            // Copy chrome.exe to root
            await io.cp(srcPath, path.join(portableRoot, 'chrome.exe'));
        } else if (file.startsWith('chromePortable-')) {
            // Skip our own portable folders
            continue;
        } else {
            // Copy everything else to version folder
            const destPath = path.join(versionDir, file);
            if (stat.isDirectory()) {
                await io.cp(srcPath, destPath, {recursive: true});
            } else {
                await io.cp(srcPath, destPath);
            }
        }
    }
    
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
  │   ├── Locales/
  │   ├── *.pak
  │   └── ...
  └── UserData/           ← Your profile (created on first run)
      ├── Default/
      └── ...

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
3. Your data is automatically saved to UserData folder
4. To move to another PC: copy entire folder

NOTES:
------
- First run creates UserData folder automatically
- All your settings, extensions, cookies are in UserData
- Safe to delete UserData folder to start fresh
- Version ${version} files are in the ${version} subfolder

For more info: https://github.com/ungoogled-software/ungoogled-chromium-windows
`;
    await fs.writeFile(path.join(portableRoot, 'README.txt'), readme);
    
    // Create .gitignore for UserData
    const gitignore = `# Ignore user data
UserData/*
!UserData/.gitkeep
`;
    await fs.writeFile(path.join(portableRoot, '.gitignore'), gitignore);
    
    // Create .gitkeep in UserData
    await fs.writeFile(path.join(userDataDir, '.gitkeep'), '');
    
    // Archive portable folder
    const archiveName = `ungoogled-chromium-${version}-${arch}-portable.zip`;
    const archivePath = path.join(buildDir, archiveName);
    
    console.log(`Creating archive: ${archiveName}`);
    await exec.exec('7z', [
        'a', '-tzip', archivePath, portableRoot, '-mx=5', '-mmt=on'
    ]);
    
    // Clean up portable folder
    await io.rmRF(portableRoot);
    
    console.log(`✓ Portable package created: ${archiveName}`);
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
        
        // Find build output directory
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
                // Try next path
            }
        }
        
        if (!buildOutDir) {
            // Search for any out directory with chrome.exe
            const globber = await glob.create('C:\\ungoogled-chromium-windows\\build\\src\\out\\*\\chrome.exe');
            const files = await globber.glob();
            if (files.length > 0) {
                buildOutDir = path.dirname(files[0]);
                console.log(`Found build directory via glob: ${buildOutDir}`);
            }
        }
        
        if (!buildOutDir) {
            throw new Error('Could not find build output directory with chrome.exe');
        }
        
        // Create portable structure
        const portableArchive = await createPortableStructure(buildOutDir, x86, arm);
        
        // Upload portable package
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
                console.error(`Upload artifact failed (attempt ${i + 1}/5): ${e}`);
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
