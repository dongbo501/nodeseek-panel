const esbuild = require('esbuild');
const fs = require('node:fs');
const path = require('node:path');

esbuild.buildSync({
  entryPoints: ['panel/frontend/scene.js'],
  outfile: 'panel/static/scene.js',
  bundle: true, minify: true, format: 'iife', target: ['es2022'],
  legalComments: 'linked',
});
fs.copyFileSync(path.join(path.dirname(require.resolve('three')), '..', 'LICENSE'), 'panel/static/three-LICENSE.txt');
console.log('Built self-hosted Three.js scene.');
