import {createHash} from 'node:crypto';
import {readdirSync,readFileSync,writeFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import tailwind from '@tailwindcss/vite';
import {defineConfig} from 'vite';

const sourceRoot=path.dirname(fileURLToPath(import.meta.url));
const outputRoot=path.resolve(sourceRoot,'../frontend');
const ignored=new Set(['node_modules','.git','dist','__pycache__']);

function sourceFiles(directory){
 return readdirSync(directory,{withFileTypes:true}).flatMap(entry=>{
	if(entry.isDirectory())return ignored.has(entry.name)?[]:sourceFiles(path.join(directory,entry.name));
	return [path.join(directory,entry.name)];
 }).sort((left,right)=>{
  const a=path.relative(sourceRoot,left).split(path.sep).join('/');
  const b=path.relative(sourceRoot,right).split(path.sep).join('/');
  return a<b?-1:a>b?1:0;
 });
}

function sourceFingerprint(){
 const digest=createHash('sha256');
 for(const file of sourceFiles(sourceRoot)){
	digest.update(path.relative(sourceRoot,file).split(path.sep).join('/'));
	digest.update('\0');
	digest.update(readFileSync(file));
	digest.update('\0');
 }
 return digest.digest('hex');
}

const sourceMarker={
 name:'aurora-source-fingerprint',
 closeBundle(){writeFileSync(path.join(outputRoot,'aurora-source.sha256'),sourceFingerprint()+'\n','utf8');},
};

export default defineConfig({
 base:'/static/',
 plugins:[tailwind(),sourceMarker],
 resolve:{alias:{'@':path.resolve(sourceRoot,'src')}},
 esbuild:{jsx:'automatic'},
 build:{outDir:outputRoot,emptyOutDir:true},
});