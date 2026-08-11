import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { pathToFileURL } from 'node:url'


const repoRoot = path.resolve(import.meta.dirname, '..', '..')

const targets = {
  executionBaseUrl: path.join(repoRoot, 'services', 'execution-plane', 'api', 'app', 'main.py'),
  chatBaseUrl: path.join(repoRoot, 'services', 'chat-plane', 'api', 'app', 'main.py'),
  controlPlaneBaseUrl: path.join(repoRoot, 'services', 'control-plane', 'src', 'main', 'java'),
}


function normalizeRoute(routePath) {
  const normalized = String(routePath || '').trim().replaceAll(/\/+/g, '/')
  if (!normalized) return '/'
  return normalized.length > 1 ? normalized.replace(/\/$/, '') : normalized
}


function routeKey(baseKey, method, routePath) {
  return `${baseKey} ${String(method).toUpperCase()} ${normalizeRoute(routePath)}`
}


function extractFastApiRoutes(baseKey, filePath) {
  const source = fs.readFileSync(filePath, 'utf8')
  const routes = []
  const pattern = /@(app|router)\.(get|post|put|patch|delete)\(\s*["']([^"']+)["']/g
  for (const match of source.matchAll(pattern)) {
    routes.push({baseKey, method: match[2].toUpperCase(), path: normalizeRoute(match[3]), source: path.relative(repoRoot, filePath)})
  }
  return routes
}


function joinRoute(prefix, suffix) {
  return normalizeRoute(`${normalizeRoute(prefix)}/${String(suffix || '').replace(/^\//, '')}`)
}


function extractSpringRoutes(baseKey, rootDir) {
  const routes = []
  const files = fs.readdirSync(rootDir, {recursive: true})
    .filter((file) => String(file).endsWith('.java'))
  for (const relativeFile of files) {
    const filePath = path.join(rootDir, relativeFile)
    const source = fs.readFileSync(filePath, 'utf8')
    const classIndex = source.indexOf(' class ')
    const classHeader = classIndex >= 0 ? source.slice(0, classIndex) : source
    const prefixMatch = classHeader.match(/@RequestMapping\(\s*["']([^"']*)["']\s*\)/)
    const prefix = prefixMatch?.[1] || ''
    const pattern = /@(Get|Post|Put|Patch|Delete)Mapping(?:\(([^)]*)\))?/g
    for (const match of source.matchAll(pattern)) {
      const annotationArguments = match[2] || ''
      const explicitPath = annotationArguments.match(/(?:value|path)\s*=\s*["']([^"']*)["']/)?.[1]
      const positionalPath = annotationArguments.match(/^\s*["']([^"']*)["']/)?.[1]
      routes.push({
        baseKey,
        method: match[1].toUpperCase(),
        path: joinRoute(prefix, explicitPath || positionalPath || ''),
        source: path.relative(repoRoot, filePath),
      })
    }
  }
  return routes
}


const catalogModuleUrl = pathToFileURL(path.join(repoRoot, 'apps', 'debug-web', 'src', 'endpoints.js')).href
const {endpointGroups} = await import(catalogModuleUrl)
const catalog = endpointGroups.flatMap((group) => group.endpoints.map((endpoint) => ({
  ...endpoint,
  group: group.group,
  method: String(endpoint.method).toUpperCase(),
  path: normalizeRoute(endpoint.path),
})))

const backendRoutes = [
  ...extractFastApiRoutes('executionBaseUrl', targets.executionBaseUrl),
  ...extractFastApiRoutes('chatBaseUrl', targets.chatBaseUrl),
  ...extractSpringRoutes('controlPlaneBaseUrl', targets.controlPlaneBaseUrl),
]

const backendKeys = new Set(backendRoutes.map((route) => routeKey(route.baseKey, route.method, route.path)))
const catalogKeys = new Set(catalog.map((route) => routeKey(route.baseKey, route.method, route.path)))
const duplicateIds = [...new Set(catalog.map((item) => item.id).filter((id, index, all) => all.indexOf(id) !== index))]
const brokenCatalogEntries = catalog.filter((route) => !backendKeys.has(routeKey(route.baseKey, route.method, route.path)))
const uncataloguedRoutes = backendRoutes.filter((route) => !catalogKeys.has(routeKey(route.baseKey, route.method, route.path)))

const report = {
  catalogEntries: catalog.length,
  backendRoutes: backendRoutes.length,
  catalogCoveragePercent: backendRoutes.length ? Number(((backendRoutes.length - uncataloguedRoutes.length) / backendRoutes.length * 100).toFixed(1)) : 0,
  duplicateIds,
  brokenCatalogEntries: brokenCatalogEntries.map(({id, group, baseKey, method, path: routePath}) => ({id, group, baseKey, method, path: routePath})),
  uncataloguedRoutes,
}

if (process.argv.includes('--json')) {
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
} else {
  console.log(`Endpoint catalog: ${report.catalogEntries} entries`)
  console.log(`Backend routes: ${report.backendRoutes}`)
  console.log(`Catalog coverage: ${report.catalogCoveragePercent}%`)
  console.log(`Duplicate endpoint IDs: ${report.duplicateIds.length}`)
  console.log(`Broken catalog entries: ${report.brokenCatalogEntries.length}`)
  console.log(`Uncatalogued backend routes: ${report.uncataloguedRoutes.length}`)
  if (report.brokenCatalogEntries.length) {
    console.log('\nBroken catalog entries:')
    for (const item of report.brokenCatalogEntries) {
      console.log(`- ${item.id}: ${item.baseKey} ${item.method} ${item.path}`)
    }
  }
}

if (duplicateIds.length || brokenCatalogEntries.length) {
  process.exitCode = 1
}
