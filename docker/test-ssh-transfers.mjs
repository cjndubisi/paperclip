// Exercises verbatim deployed transfer functions, in a separate Node process.
// Usage: node test-ssh-transfers.mjs /path/to/ssh.ts bundle|upload-error|download-error
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { stripTypeScriptTypes } from 'node:module';
const [sourcePath, mode = 'bundle'] = process.argv.slice(2);
const source = await fs.readFile(sourcePath, 'utf8');
const names = ['streamLocalFileToSsh', 'streamSshToLocalFile', 'syncDirectoryToSsh', 'syncDirectoryFromSsh'];
// Slice top-level declarations without rewriting their implementations.
const declarations = [...source.matchAll(/^(?:export )?(?:async )?function \w+/gm)];
function extract(name) {
  const i = declarations.findIndex(m => m[0].endsWith(' '+name));
  assert.ok(i >= 0, name);
  return source.slice(declarations[i].index, declarations[i+1]?.index ?? source.length).replace(/^export /, '');
}
let helpers = '';
if (source.includes('function buildSshTransferCommand')) helpers += extract('buildSshTransferCommand');
const prelude = `
import fs from 'node:fs/promises';
import {createReadStream,createWriteStream} from 'node:fs';
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {Transform} from 'node:stream';
import os from 'node:os';
import path from 'node:path';
const shellQuote = value => "'" + value.replaceAll("'", "'\\\\''") + "'";
async function createSshAuthArgs() {return {args:['-o','BatchMode=yes','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null'],cleanup:async()=>{}};}
const tarExcludeArgs = xs => (xs ?? []).flatMap(x => ['--exclude',x]);
const tarSpawnEnv = () => ({...process.env, COPYFILE_DISABLE:'1'});
const createTransferProgress = () => ({counter:new Transform({transform(c,e,cb){cb(null,c)}}),finish:async()=>{},fail:async()=>{}});
const estimateLocalDirSize = async()=>0;
const probeRemoteDirSize = async()=>0;
const clearLocalDirectory = async d => {for(const n of await fs.readdir(d)) await fs.rm(path.join(d,n),{recursive:true,force:true});};
const copyDirectoryContents = async (s,d)=>{for(const n of await fs.readdir(s)) await fs.cp(path.join(s,n),path.join(d,n),{recursive:true});};
`;
const dir = await fs.mkdtemp(path.join(os.tmpdir(),'pc-transfer-test-'));
try {
  const modulePath = path.join(dir, 'transfers.mjs');
  await fs.writeFile(modulePath, prelude + stripTypeScriptTypes(helpers + names.map(extract).join('\n')) + '\nexport {'+names.join(',')+'};');
  const api = await import(modulePath);
  const spec = {host:process.env.TEST_SSH_HOST ?? 'pc-ssh-dynamic',username:'sprite',port:22};
  const q = v => "'"+v.replaceAll("'", "'\\''")+"'";
  const git = (cwd,...args) => execFileSync('git',['-C',cwd,...args],{encoding:'utf8'}).trim();
  if (mode === 'bundle') {
    const repo = path.join(dir,'repo'); await fs.mkdir(repo);
    git(repo,'init','-q'); git(repo,'config','user.name','Transfer test'); git(repo,'config','user.email','transfer-test@example.invalid');
    const bytes = randomBytes(Number(process.env.TEST_BYTES ?? 2*1024*1024));
    await fs.writeFile(path.join(repo,'binary.dat'),bytes); git(repo,'add','.'); git(repo,'commit','-qm','binary fixture');
    const head = git(repo,'rev-parse','HEAD'); const bundle = path.join(dir,'input.bundle'); git(repo,'bundle','create',bundle,'HEAD');
    const remote = '/tmp/'+path.basename(dir)+"-quote' space";
    const remoteScript = ['set -e',`mkdir -p ${q(remote)}`,`cat > ${q(remote+'/in.bundle')}`,`git clone -q ${q(remote+'/in.bundle')} ${q(remote+'/repo')}`].join('\n');
    await api.streamLocalFileToSsh({spec,localFile:bundle,remoteScript});
    const output = path.join(dir,'output.bundle');
    await api.streamSshToLocalFile({spec,localFile:output,remoteScript:['set -e',`git -C ${q(remote+'/repo')} bundle create ${q(remote+'/out.bundle')} HEAD`,`cat ${q(remote+'/out.bundle')}`,`rm -rf ${q(remote)}`].join('\n')});
    const clone = path.join(dir,'clone'); execFileSync('git',['clone','-q',output,clone]);
    assert.equal(git(clone,'rev-parse','HEAD'),head); assert.deepEqual(await fs.readFile(path.join(clone,'binary.dat')),bytes);
    await api.syncDirectoryToSsh({spec,localDir:repo,remoteDir:remote,exclude:['.git'],onProgress:async()=>{}});
    const restored = path.join(dir,'restored'); await fs.mkdir(restored);
    await api.syncDirectoryFromSsh({spec,localDir:restored,remoteDir:remote,onProgress:async()=>{}});
    assert.deepEqual(await fs.readFile(path.join(restored,'binary.dat')),bytes);
    await api.streamSshToLocalFile({spec,localFile:path.join(dir,'cleanup'),remoteScript:`rm -rf ${q(remote)}`});
    console.log(JSON.stringify({test:mode,status:'PASS',head,bytes:bytes.length,directoryRoundtrip:true}));
  } else {
    const bin = path.join(dir,'bin'); await fs.mkdir(bin);
    const sink = '#!/bin/sh\nexec 0<&-\nprintf "intentional transfer peer failure\\n" >&2\nsleep 0.1\nexit 42\n';
    const producer = '#!/bin/sh\nexec /bin/cat '+q(path.join(dir,'large'))+'\n';
    await fs.writeFile(path.join(dir,'large'),Buffer.alloc(32*1024*1024,17));
    await fs.writeFile(path.join(bin,'ssh'),mode==='download-error'?producer:sink,{mode:0o755});
    if(mode==='download-error') await fs.writeFile(path.join(bin,'tar'),sink,{mode:0o755});
    process.env.PATH=bin+':'+process.env.PATH;
    const local = path.join(dir,'local'); await fs.mkdir(local);
    await fs.copyFile(path.join(dir,'large'),path.join(local,'large'));
    const calls = mode==='download-error' ? [()=>api.syncDirectoryFromSsh({spec,remoteDir:'/unused',localDir:local})] : [()=>api.streamLocalFileToSsh({spec,localFile:path.join(dir,'large'),remoteScript:'exit 42'}),()=>api.syncDirectoryToSsh({spec,localDir:local,remoteDir:'/unused'})];
    for(const call of calls) {
      await assert.rejects(call, /intentional transfer peer failure/);
      await new Promise(r=>setTimeout(r,150));
    }
    console.log(JSON.stringify({test:mode,status:'PASS',contained:calls.length}));
  }
} finally { await fs.rm(dir,{recursive:true,force:true}); }
