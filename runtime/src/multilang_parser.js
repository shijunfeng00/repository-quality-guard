'use strict';

const fs = require('fs');
const path = require('path');
const dependencyRoot = process.env.RQG_NODE_MODULES;
if (!dependencyRoot) throw new Error('RQG_NODE_MODULES is required');
const ts = require(path.join(dependencyRoot, 'typescript', 'lib', 'typescript.js'));
const postcss = require(path.join(dependencyRoot, 'postcss', 'lib', 'postcss.js'));

function readInput() {
  const raw = fs.readFileSync(0, 'utf8');
  const value = JSON.parse(raw || '[]');
  if (!Array.isArray(value)) throw new Error('multilang parser input must be an array');
  return value;
}

function nodeName(node, sf) {
  if (node.name && typeof node.name.getText === 'function') return node.name.getText(sf);
  const p = node.parent;
  if (p && ts.isVariableDeclaration(p) && p.name) return p.name.getText(sf);
  if (p && ts.isPropertyAssignment(p) && p.name) return p.name.getText(sf);
  return `<anonymous@${sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1}>`;
}

function isFunctionLike(node) {
  return ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node) ||
    ts.isArrowFunction(node) || ts.isMethodDeclaration(node) ||
    ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node);
}


function scopeKey(node, sf) {
  let current = node.parent;
  while (current) {
    if (isFunctionLike(current)) {
      const name = nodeName(current, sf);
      const line = sf.getLineAndCharacterOfPosition(current.getStart(sf)).line + 1;
      return `function:${name}@${line}`;
    }
    if (ts.isClassDeclaration(current) || ts.isClassExpression(current)) {
      const name = current.name ? current.name.getText(sf) : '<anonymous-class>';
      const line = sf.getLineAndCharacterOfPosition(current.getStart(sf)).line + 1;
      return `class:${name}@${line}`;
    }
    if (ts.isObjectLiteralExpression(current)) {
      const parent = current.parent;
      let name = '<object>';
      if (parent && ts.isVariableDeclaration(parent) && parent.name) name = parent.name.getText(sf);
      else if (parent && ts.isPropertyAssignment(parent) && parent.name) name = parent.name.getText(sf);
      const line = sf.getLineAndCharacterOfPosition(current.getStart(sf)).line + 1;
      return `object:${name}@${line}`;
    }
    current = current.parent;
  }
  return 'source-file';
}
function callableKind(node) {
  return ts.isMethodDeclaration(node) || ts.isGetAccessorDeclaration(node) ||
    ts.isSetAccessorDeclaration(node) ? 'method' : 'function';
}

function callName(expr, sf) {
  if (ts.isIdentifier(expr)) return expr.text;
  if (ts.isPropertyAccessExpression(expr)) return expr.name.text;
  if (ts.isElementAccessExpression(expr)) {
    const a = expr.argumentExpression;
    if (a && (ts.isStringLiteral(a) || ts.isNumericLiteral(a))) return String(a.text);
  }
  const text = expr.getText(sf);
  return text.length <= 120 ? text : text.slice(0, 120);
}

function hasFunctionLikeDescendant(node) {
  let found = false;
  function walk(n) {
    if (found) return;
    if (n !== node && isFunctionLike(n)) { found = true; return; }
    ts.forEachChild(n, walk);
  }
  walk(node);
  return found;
}

function simpleCallable(expr) {
  if (ts.isIdentifier(expr)) return true;
  if (!ts.isPropertyAccessExpression(expr)) return false;
  let current = expr.expression;
  while (ts.isPropertyAccessExpression(current)) current = current.expression;
  return ts.isIdentifier(current) || current.kind === ts.SyntaxKind.ThisKeyword;
}

function simpleWrapperArgument(expr) {
  const current = unwrap(expr);
  if (!current) return false;
  if (ts.isIdentifier(current) || current.kind === ts.SyntaxKind.ThisKeyword ||
      ts.isStringLiteral(current) || ts.isNumericLiteral(current) ||
      current.kind === ts.SyntaxKind.TrueKeyword || current.kind === ts.SyntaxKind.FalseKeyword ||
      current.kind === ts.SyntaxKind.NullKeyword) return true;
  if (ts.isSpreadElement(current)) return simpleWrapperArgument(current.expression);
  return false;
}

function wrapperTarget(node, sf) {
  let call = null;
  if (!node.body) return '';
  if (ts.isBlock(node.body) && node.body.statements.length === 1) {
    const statement = node.body.statements[0];
    if (ts.isReturnStatement(statement) && statement.expression && ts.isCallExpression(statement.expression)) {
      call = statement.expression;
    } else if (ts.isExpressionStatement(statement) && ts.isCallExpression(statement.expression)) {
      call = statement.expression;
    }
  } else if (!ts.isBlock(node.body) && ts.isCallExpression(node.body)) {
    call = node.body;
  }
  if (!call || !simpleCallable(call.expression)) return '';
  if (call.arguments.some(hasFunctionLikeDescendant)) return '';
  if (!call.arguments.every(simpleWrapperArgument)) return '';
  return callName(call.expression, sf);
}

function normalizedShape(root) {
  const parts = [];
  function walk(n) {
    let tag = ts.SyntaxKind[n.kind];
    if (ts.isIdentifier(n)) tag = 'Identifier';
    else if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) tag = 'StringLiteral';
    else if (ts.isNumericLiteral(n)) tag = 'NumericLiteral';
    else if (n.kind === ts.SyntaxKind.TrueKeyword || n.kind === ts.SyntaxKind.FalseKeyword) tag = 'BooleanLiteral';
    else if (ts.isBinaryExpression(n)) tag = `Binary:${ts.SyntaxKind[n.operatorToken.kind]}`;
    else if (ts.isPrefixUnaryExpression(n) || ts.isPostfixUnaryExpression(n)) tag = `Unary:${n.operator}`;
    else if (ts.isPropertyAccessExpression(n)) tag = `Property:${n.name.text}`;
    parts.push(tag);
    ts.forEachChild(n, walk);
  }
  walk(root);
  return parts.join(' ');
}

function functionMetrics(node, sf) {
  let branches = 0;
  let maxNesting = 0;
  let nodeCount = 0;
  let nestedDefs = 0;
  const calls = [];
  const body = node.body || node;
  function visit(n, depth, root = false) {
    nodeCount += 1;
    if (!root && isFunctionLike(n)) { nestedDefs += 1; return; }
    if (ts.isIfStatement(n) || ts.isForStatement(n) || ts.isForInStatement(n) ||
        ts.isForOfStatement(n) || ts.isWhileStatement(n) || ts.isDoStatement(n) ||
        ts.isCaseClause(n) || ts.isConditionalExpression(n) || ts.isCatchClause(n)) branches += 1;
    if (n.kind === ts.SyntaxKind.AmpersandAmpersandToken ||
        n.kind === ts.SyntaxKind.BarBarToken || n.kind === ts.SyntaxKind.QuestionQuestionToken) branches += 1;
    const nesting = ts.isIfStatement(n) || ts.isForStatement(n) || ts.isForInStatement(n) ||
      ts.isForOfStatement(n) || ts.isWhileStatement(n) || ts.isDoStatement(n) ||
      ts.isSwitchStatement(n) || ts.isTryStatement(n);
    const nextDepth = depth + (nesting ? 1 : 0);
    maxNesting = Math.max(maxNesting, nextDepth);
    if (ts.isCallExpression(n)) calls.push(callName(n.expression, sf));
    ts.forEachChild(n, child => visit(child, nextDepth, false));
  }
  visit(body, 0, true);
  return {
    branches, max_nesting: maxNesting, node_count: nodeCount, nested_defs: nestedDefs,
    calls, wrapper_target: wrapperTarget(node, sf), shape: normalizedShape(body)
  };
}

function unwrap(node) {
  let current = node;
  while (current && (ts.isParenthesizedExpression(current) ||
         (ts.isAsExpression && ts.isAsExpression(current)) ||
         (ts.isTypeAssertionExpression && ts.isTypeAssertionExpression(current)))) current = current.expression;
  return current;
}

function propertyName(node, sf) {
  if (!node) return '';
  if (ts.isIdentifier(node)) return node.text;
  if (ts.isStringLiteral(node) || ts.isNumericLiteral(node)) return String(node.text);
  return node.getText ? node.getText(sf) : '';
}

function ownerRefs(node) {
  const aliases = new Set(['this']);
  const calls = [];
  const refs = [];
  function discover(n) {
    if (ts.isVariableDeclaration(n) && ts.isIdentifier(n.name) && n.initializer) {
      const init = unwrap(n.initializer);
      if (init && init.kind === ts.SyntaxKind.ThisKeyword) aliases.add(n.name.text);
    }
    ts.forEachChild(n, discover);
  }
  discover(node);
  function walk(n) {
    if (ts.isPropertyAccessExpression(n)) {
      const recv = unwrap(n.expression);
      const recvName = recv.kind === ts.SyntaxKind.ThisKeyword ? 'this' : (ts.isIdentifier(recv) ? recv.text : '');
      if (aliases.has(recvName)) refs.push(n.name.text);
    }
    if (ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression)) {
      const recv = unwrap(n.expression.expression);
      const recvName = recv.kind === ts.SyntaxKind.ThisKeyword ? 'this' : (ts.isIdentifier(recv) ? recv.text : '');
      if (aliases.has(recvName)) calls.push(n.expression.name.text);
    }
    ts.forEachChild(n, walk);
  }
  walk(node);
  return {calls, refs};
}

function scriptKind(language, filePath) {
  if (language === 'typescript') return filePath.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  return filePath.endsWith('.jsx') ? ts.ScriptKind.JSX : ts.ScriptKind.JS;
}

function parseScript(item) {
  const sf = ts.createSourceFile(item.path, item.source, ts.ScriptTarget.Latest, true, scriptKind(item.language, item.path));
  const definitions = [];
  const stack = [];
  function walk(node) {
    let pushed = false;
    if (isFunctionLike(node)) {
      const name = nodeName(node, sf);
      const start = sf.getLineAndCharacterOfPosition(node.getStart(sf));
      const end = sf.getLineAndCharacterOfPosition(node.end);
      const qualname = [...stack, name].join('.');
      definitions.push({
        name, qualname, kind: callableKind(node), declaration_kind: ts.SyntaxKind[node.kind],
        scope_key: scopeKey(node, sf), line: start.line + 1, column: start.character + 1,
        end_line: end.line + 1, parameter_count: (node.parameters || []).length,
        parameter_names: (node.parameters || []).map(p => p.name.getText(sf)),
        ...functionMetrics(node, sf)
      });
      stack.push(name); pushed = true;
    }
    ts.forEachChild(node, walk);
    if (pushed) stack.pop();
  }
  walk(sf);

  const identifierRefs = Object.create(null);
  const symbolRefs = Object.create(null);
  function countIdentifiers(node) {
    if (ts.isIdentifier(node)) identifierRefs[node.text] = (identifierRefs[node.text] || 0) + 1;
    if (ts.isPropertyAssignment(node) && ts.isStringLiteral(node.initializer) &&
        /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(node.initializer.text)) {
      const name = node.initializer.text;
      symbolRefs[name] = (symbolRefs[name] || 0) + 1;
    }
    ts.forEachChild(node, countIdentifiers);
  }
  countIdentifiers(sf);

  const imports = [];
  const bags = [];
  const classes = [];
  const compositions = [];
  for (const statement of sf.statements) {
    if (ts.isImportDeclaration(statement) && statement.moduleSpecifier && ts.isStringLiteral(statement.moduleSpecifier)) {
      imports.push(statement.moduleSpecifier.text);
    }
    if (ts.isVariableStatement(statement)) {
      for (const decl of statement.declarationList.declarations) {
        if (!ts.isIdentifier(decl.name) || !decl.initializer || !ts.isObjectLiteralExpression(decl.initializer)) continue;
        const methods = [];
        for (const prop of decl.initializer.properties) {
          let bodyNode = null;
          if (ts.isMethodDeclaration(prop)) bodyNode = prop;
          else if (ts.isPropertyAssignment(prop) && (ts.isFunctionExpression(prop.initializer) || ts.isArrowFunction(prop.initializer))) bodyNode = prop.initializer;
          if (!bodyNode || !bodyNode.body) continue;
          const refs = ownerRefs(bodyNode);
          methods.push({name: propertyName(prop.name, sf), line: sf.getLineAndCharacterOfPosition(prop.getStart(sf)).line + 1, calls: refs.calls, refs: refs.refs});
        }
        if (methods.length) bags.push({name: decl.name.text, methods});
      }
    }
    if (ts.isClassDeclaration(statement) && statement.name) {
      const info = {name: statement.name.text, methods: [], constructor_fields: []};
      for (const member of statement.members) {
        if (ts.isConstructorDeclaration(member)) {
          function walkConstructor(node) {
            if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
                ts.isPropertyAccessExpression(node.left) && node.left.expression.kind === ts.SyntaxKind.ThisKeyword) {
              info.constructor_fields.push(node.left.name.text);
            }
            ts.forEachChild(node, walkConstructor);
          }
          walkConstructor(member);
        } else if (ts.isMethodDeclaration(member) && member.body) {
          const refs = ownerRefs(member);
          info.methods.push({name: propertyName(member.name, sf), line: sf.getLineAndCharacterOfPosition(member.getStart(sf)).line + 1, calls: refs.calls, refs: refs.refs});
        }
      }
      info.constructor_fields = [...new Set(info.constructor_fields)];
      classes.push(info);
    }
  }
  function walkComposition(node) {
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression) &&
        node.expression.expression.getText(sf) === 'Object' && node.expression.name.text === 'assign' && node.arguments.length >= 2) {
      const first = node.arguments[0];
      if (ts.isPropertyAccessExpression(first) && first.name.text === 'prototype') {
        compositions.push({class_name: first.expression.getText(sf), bags: node.arguments.slice(1).map(arg => arg.getText(sf))});
      }
    }
    ts.forEachChild(node, walkComposition);
  }
  walkComposition(sf);

  return {
    path: item.path, language: item.language, bytes: Buffer.byteLength(item.source),
    lines: item.source.split(/\r?\n/).length,
    diagnostics: sf.parseDiagnostics.map(d => ({
      start: d.start || 0, length: d.length || 0,
      message: ts.flattenDiagnosticMessageText(d.messageText, ' ')
    })),
    definitions, identifier_refs: identifierRefs, symbol_refs: symbolRefs, imports, bags, classes, compositions,
    type_escapes: (item.source.match(/@type\s*\{\s*any\s*\}/g) || []).length +
      (item.source.match(/@typedef\s*\{\s*any\s*\}/g) || []).length
  };
}

function parseCss(item) {
  const errors = [];
  const rules = [];
  let root;
  try { root = postcss.parse(item.source, {from: item.path}); }
  catch (error) {
    errors.push(String(error));
    return {path: item.path, language: item.language, bytes: Buffer.byteLength(item.source), lines: item.source.split(/\r?\n/).length, errors, rules};
  }
  root.walkRules(rule => {
    const declarations = [];
    rule.walkDecls(decl => declarations.push({
      prop: decl.prop, value: decl.value, important: decl.important,
      line: decl.source && decl.source.start ? decl.source.start.line : 0
    }));
    const normalized = declarations.map(d => `${d.prop}:${d.value}${d.important ? '!important' : ''}`).sort().join(';');
    rules.push({
      selector: rule.selector,
      line: rule.source && rule.source.start ? rule.source.start.line : 0,
      end_line: rule.source && rule.source.end ? rule.source.end.line : 0,
      declarations: declarations.length,
      normalized
    });
  });
  return {path: item.path, language: item.language, bytes: Buffer.byteLength(item.source), lines: item.source.split(/\r?\n/).length, errors, rules};
}

const output = [];
for (const item of readInput()) {
  if (!item || typeof item.path !== 'string' || typeof item.source !== 'string') continue;
  if (item.language === 'javascript' || item.language === 'typescript') output.push(parseScript(item));
  else if (item.language === 'css') output.push(parseCss(item));
}
process.stdout.write(JSON.stringify(output));
