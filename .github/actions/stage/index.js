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
    const portableRoot = path.join('C:\\ungoogled-chromium-windows\\build', `chromePortable-${arch}`);
    
    // Read version from chrome.exe
    let version = '';
    const {stdout} = await exec.getExecOutput('powershell', [
        '-Command',
        `(Get-Item "${buildDir}\\chrome.exe").VersionInfo.FileVersion`
    ]);
    version = stdout.trim();
    
    console.log(`Detected version: ${version}`);
    
    const versionDir = path.join(portableRoot, version);
    const userDataDir = path.join(portableRoot, 'UserData');
    
    // Create directories
    await io.mkdirP(versionDir);
    await io.mkdirP(userDataDir);
    
    // Copy all files except chrome.exe to version folder
    await exec.exec('robocopy', [
        buildDir,
        versionDir,
        '/E', '/XF', 'chrome.exe', '/NFL', '/NDL', '/NJH', '/NJS', '/nc', '/ns', '/np'
    ], {ignoreReturnCode: true}); // robocopy returns 1 on success
    
    // Copy chrome.exe to root
    await io.cp(path.join(buildDir, 'chrome.exe'), path.join(portableRoot, 'chrome.exe'));
    
    // Create README
    const readme = `Ungoogled Chromium Portable ${version} (${arch})

Structure:
  chrome.exe - Main launcher
  ${version}/ - Browser files
  UserData/ - Your profile data (created on first run)

Features:
  ✓ Symmetric encryption (no --disable-encryption needed)
  ✓ Portable profile (no --user-data-dir needed)
  ✓ No machine-id binding
  ✓ Transfer between computers without data loss

Just run chrome.exe!
`;
    await fs.writeFile(path.join(portableRoot, 'README.txt'), readme);
    
    // Archive portable folder
    const archiveName = `ungoogled-chromium-${version}-${arch}-portable.zip`;
    const archivePath = path.join(buildDir, archiveName);
    
    await exec.exec('7z', [
        'a', '-tzip', archivePath, portableRoot, '-mx=5'
    ]);
    
    console.log(`Portable package created: ${archiveName}`);
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
        
        // Find original build output directory
        const globber = await glob.create('C:\\ungoogled-chromium-windows\\build\\src\\out\\*',
            {matchDirectories: true});
        const outDirs = await globber.glob();
        let buildOutDir = null;
        
        for (const dir of outDirs) {
            const chromeExePath = path.join(dir, 'chrome.exe');
            try {
                await fs.access(chromeExePath);
                buildOutDir = dir;
                break;
            } catch (e) {
                // continue searching
            }
        }
        
        if (!buildOutDir) {
            throw new Error('Could not find build output directory with chrome.exe');
        }
        
        console.log(`Found build directory: ${buildOutDir}`);
        
        // Create portable structure
        const portableArchive = await createPortableStructure(buildOutDir, x86, arm);
        
        // Upload portable package as artifact
        const finalArtifactName = x86 ? 'chromium-x86' : (arm ? 'chromium-arm' : 'chromium');
        for (let i = 0; i < 5; ++i) {
            try {
                await artifact.deleteArtifact(finalArtifactName);
            } catch (e) {
                // ignored
            }
            try {
                await artifact.uploadArtifact(finalArtifactName, [portableArchive],
                    'C:\\ungoogled-chromium-windows\\build', {retentionDays: 1, compressionLevel: 0});
                break;
            } catch (e) {
                console.error(`Upload artifact failed: ${e}`);
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
