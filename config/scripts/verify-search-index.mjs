#!/usr/bin/env node
// Read-only verification for the reviewed QMD 2.0.1 schema/tokenizer.
import {createRequire} from 'node:module';
import {readFileSync,realpathSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';

const sha=text=>createHash('sha256').update(text).digest('hex');

export function readSource(vault,path,issues){
  try{return readFileSync(resolve(vault,path),'utf8');}
  catch(error){
    if(error.code!=='ENOENT'&&error.code!=='ENOTDIR')throw error;
    issues.push({path,reason:'source-changed-during-audit'});
    return null;
  }
}

export function missingChunks(hash,chunks,metadata,vectorKeys,model){
  const issues=[];
  for(let seq=0;seq<chunks.length;seq++){
    const key=`${hash}_${seq}`;const row=metadata.get(key);
    if(!row||!vectorKeys.has(key))issues.push({hash,seq,reason:'missing-chunk'});
    else if(row.pos!==chunks[seq].pos||row.model!==model)issues.push({hash,seq,reason:'chunk-position-or-model-mismatch'});
  }
  return issues;
}

export async function verify({packageRoot,index,vault,config,inventoryOnly=false}){
  const require=createRequire(resolve(packageRoot,'package.json'));
  if(JSON.parse(readFileSync(resolve(packageRoot,'package.json'),'utf8')).version!=='2.0.1')throw Error('Unsupported QMD version');
  const YAML=require('yaml');const glob=require('fast-glob');const Database=require('better-sqlite3');
  const api=await import(pathToFileURL(resolve(packageRoot,'dist/store.js')).href);
  const {disposeDefaultLlamaCpp}=await import(pathToFileURL(resolve(packageRoot,'dist/llm.js')).href);
  const {loadSqliteVec}=await import(pathToFileURL(resolve(packageRoot,'dist/db.js')).href);
  const raw=readFileSync(config);const settings=YAML.parse(raw.toString());
  const entries=Object.entries(settings?.collections||{});
  if(entries.length!==1)throw Error('Exactly one collection required');
  const [collection,scope]=entries[0];
  if(typeof scope.path!=='string'||!scope.path.trim()||realpathSync(scope.path)!==realpathSync(vault)||scope.pattern!=='**/*.md'||scope.update)throw Error('Unreviewed collection scope');
  if(scope.ignore!==undefined&&(!Array.isArray(scope.ignore)||scope.ignore.some(x=>typeof x!=='string')))throw Error('Invalid ignore patterns');
  const sources=new Map();const observed=new Map();const sourceIssues=[];let empty=0;
  const listFiles=async()=> (await glob(scope.pattern,{cwd:vault,onlyFiles:true,followSymbolicLinks:false,dot:false,
    ignore:[...['node_modules','.git','.cache','vendor','dist','build'].map(n=>`**/${n}/**`),...(scope.ignore||[])]})).filter(name=>!name.split('/').some(n=>n.startsWith('.'))).sort();
  const names=await listFiles();
  for(const name of names){
    if(name.split('/').some(n=>n.startsWith('.')))continue;
    const body=readSource(vault,name,sourceIssues);
    if(body===null)continue;
    observed.set(name,sha(body));
    if(!body.trim()){empty++;continue;}
    const key=api.handelize(name);
    if(sources.has(key))sourceIssues.push({path:key,reason:'normalized-path-collision'});
    sources.set(key,{path:name,hash:sha(body)});
  }
  if(inventoryOnly){
    if(JSON.stringify(await listFiles())!==JSON.stringify(names))sourceIssues.push({reason:'source-paths-changed-during-audit'});
    if(!readFileSync(config).equals(raw))sourceIssues.push({reason:'configuration-changed-during-audit'});
    return {as_of:new Date().toISOString(),mode:'source-inventory',qmd_version:'2.0.1',collection,
      config_sha256:sha(raw),source_files:sources.size,empty_files:empty,source_issues:sourceIssues,
      all_checks_pass:!sourceIssues.length,
      limits:'Read-only source inventory before indexing; does not verify index health or future source availability.'};
  }
  const db=new Database(index,{readonly:true,fileMustExist:true});loadSqliteVec(db);db.exec('BEGIN');
  try{
    const registered=db.prepare('SELECT name,path,pattern FROM store_collections').all();
    if(registered.length!==1||registered[0].name!==collection||realpathSync(registered[0].path)!==realpathSync(vault)||registered[0].pattern!==scope.pattern)throw Error('Index collection metadata does not match reviewed scope');
    if(db.prepare('SELECT COUNT(*) AS n FROM documents WHERE active=1 AND collection<>?').get(collection).n)sourceIssues.push({reason:'unexpected-active-collection'});
    const rows=db.prepare('SELECT d.path,d.hash,c.doc FROM documents d LEFT JOIN content c ON c.hash=d.hash WHERE d.active=1 AND d.collection=?').all(collection);
    const seen=new Set();const hashes=new Map();
    for(const row of rows){
      seen.add(row.path);
      if(row.doc===null){sourceIssues.push({path:row.path,reason:'index-content-missing'});continue;}
      hashes.set(row.hash,row.doc);
      const source=sources.get(row.path);
      if(!source)sourceIssues.push({path:row.path,reason:'indexed-source-no-longer-in-scope'});
      else if(sha(row.doc)!==row.hash)sourceIssues.push({path:row.path,reason:'index-content-corrupt'});
      else if(source.hash!==row.hash)sourceIssues.push({path:row.path,reason:'source-content-changed'});
    }
    for(const key of sources.keys())if(!seen.has(key))sourceIssues.push({path:key,reason:'source-not-indexed'});
    const metaRows=db.prepare('SELECT hash,seq,pos,model FROM content_vectors').all();
    const metadata=new Map(metaRows.map(r=>[`${r.hash}_${r.seq}`,r]));
    const vectorKeys=new Set(db.prepare('SELECT hash_seq FROM vectors_vec').all().map(r=>r.hash_seq));
    const chunkIssues=[];const expectedKeys=new Set();let expected=0;
    for(const [hash,body] of hashes){
      const chunks=await api.chunkDocumentByTokens(body);expected+=chunks.length;
      for(let seq=0;seq<chunks.length;seq++)expectedKeys.add(`${hash}_${seq}`);
      chunkIssues.push(...missingChunks(hash,chunks,metadata,vectorKeys,api.DEFAULT_EMBED_MODEL));
    }
    for(const row of metaRows)if(hashes.has(row.hash)&&!expectedKeys.has(`${row.hash}_${row.seq}`))chunkIssues.push({hash:row.hash,seq:row.seq,reason:'unexpected-active-chunk'});
    for(const key of vectorKeys)if(hashes.has(key.slice(0,64))&&!expectedKeys.has(key))chunkIssues.push({key,reason:'unexpected-active-vector'});
    // A lengthy tokenizer audit must not silently accept files changed since enumeration.
    if(JSON.stringify(await listFiles())!==JSON.stringify(names))sourceIssues.push({reason:'source-paths-changed-during-audit'});
    for(const [path,hash] of observed){
      const body=readSource(vault,path,sourceIssues);
      if(body!==null&&sha(body)!==hash)sourceIssues.push({path,reason:'source-changed-during-audit'});
    }
    if(!readFileSync(config).equals(raw))sourceIssues.push({reason:'configuration-changed-during-audit'});
    return {as_of:new Date().toISOString(),qmd_version:'2.0.1',collection,config_sha256:sha(raw),source_files:sources.size,
      empty_files:empty,indexed_documents:rows.length,indexed_hashes:hashes.size,expected_chunks:expected,
      source_issues:sourceIssues,chunk_issues:chunkIssues,all_checks_pass:!sourceIssues.length&&!chunkIssues.length,
      limits:'Checks observed source contents and complete expected chunk presence/positions/model. Does not judge retrieval relevance, factual accuracy, or changes after this observation.'};
  }finally{
    try{db.exec('ROLLBACK');db.close();}
    finally{await disposeDefaultLlamaCpp();}
  }
}

async function main(){
  const [packageRoot,index,vault,config,mode]=process.argv.slice(2);
  if(!packageRoot||!index||!vault||!config)throw Error('Usage: verify-search-index.mjs QMD_PACKAGE INDEX VAULT CONFIG');
  if(mode!==undefined&&mode!=='--inventory-only')throw Error('Unsupported mode');
  const result=await verify({packageRoot,index,vault,config,inventoryOnly:mode==='--inventory-only'});
  process.stdout.write(JSON.stringify(result)+'\n');process.exit(result.all_checks_pass?0:2);
}
if(process.argv[1]&&realpathSync(process.argv[1])===fileURLToPath(import.meta.url))main().catch(error=>{process.stdout.write(JSON.stringify({all_checks_pass:false,error:String(error)})+'\n');process.exit(2);});
