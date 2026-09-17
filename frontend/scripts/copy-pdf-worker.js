const fs = require("fs");
const path = require("path");

const source = require.resolve("pdfjs-dist/build/pdf.worker.min.mjs");
const target = path.resolve(__dirname, "../public/pdf.worker.min.mjs");

fs.copyFileSync(source, target);
console.log("[pdfjs] worker copied to public/pdf.worker.min.mjs");
