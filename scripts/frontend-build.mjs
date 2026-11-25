// Invoke the pinned Vencord production build without a package-manager install.
// Run from the managed Vencord checkout (or the corresponding source archive).
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

if (process.argv.length !== 2) throw new Error("No build arguments accepted");
process.argv.push("--standalone", "--disable-updater");
const upstream = file => pathToFileURL(resolve("scripts/build", file)).href;
const { commonOpts, watch, IS_DEV, IS_REPORTER, IS_STANDALONE, IS_UPDATER_DISABLED } =
    await import(upstream("common.mjs"));
if (watch || IS_DEV || IS_REPORTER || !IS_STANDALONE || !IS_UPDATER_DISABLED ||
    commonOpts.bundle !== true || commonOpts.minify !== true ||
    commonOpts.sourcemap !== "external" || commonOpts.legalComments !== "linked") {
    throw new Error("Unexpected Vencord production build configuration");
}
console.log("Verified production configuration: bundled, minified, external maps, linked notices, updater disabled.");
await import(upstream("build.mjs"));
