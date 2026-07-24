#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const input = process.argv[2];
if (!input) {
  console.error('用法: node validate-tool.js <课件 HTML 路径>');
  process.exit(2);
}

const target = path.resolve(input);
if (!fs.existsSync(target) || !fs.statSync(target).isFile()) {
  console.error(`文件不存在: ${target}`);
  process.exit(1);
}

const html = fs.readFileSync(target, 'utf8');
const checks = [
  ['包含 UTF-8 声明', /<meta[^>]+charset=["']?utf-8/i.test(html)],
  ['包含 viewport', /<meta[^>]+name=["']viewport["']/i.test(html)],
  ['包含课件页面', /<section[^>]+class=["'][^"']*\bslide\b/i.test(html)],
  ['包含页面标题', /<title>\s*[^<]+\s*<\/title>/i.test(html)],
];

let failed = false;
for (const [label, passed] of checks) {
  console.log(`${passed ? 'PASS' : 'FAIL'}  ${label}`);
  failed ||= !passed;
}

if (failed) process.exit(1);
console.log(`验证通过: ${target}`);

