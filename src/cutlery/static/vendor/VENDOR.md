# Vendored browser libraries and fonts

Unmodified copies, loaded from this directory only (no CDN). Update by replacing the file, the version in its
name and the checksum here, then run the browser tests.

| File | Package | Licence | Source | SHA-256 |
|---|---|---|---|---|
| mediabunny-1.64.0.min.mjs | mediabunny 1.64.0 (`dist/bundles/mediabunny.min.mjs`) | MPL-2.0 (LICENSE-mediabunny.txt) | https://registry.npmjs.org/mediabunny/-/mediabunny-1.64.0.tgz | c2d0782ed85a754a95585250913227df50010bed45a6d3a3c6e0b017004a8b01 |
| mediabunny-aac-encoder-1.64.0.min.mjs | @mediabunny/aac-encoder 1.64.0 (`dist/bundles/mediabunny-aac-encoder.min.mjs`) | MPL-2.0 (LICENSE-mediabunny-aac-encoder.txt) | https://registry.npmjs.org/@mediabunny/aac-encoder/-/aac-encoder-1.64.0.tgz | f63b6866dceb64450f88a1b1000c3e7a7209fbe3f704cc108424a5fe47cfc840 (modified, see below) |
| tus-js-client-4.3.1.min.js | tus-js-client 4.3.1 (`dist/tus.min.js`) | MIT (LICENSE-tus-js-client.txt) | https://registry.npmjs.org/tus-js-client/-/tus-js-client-4.3.1.tgz | 8cbb1b63fccc3bba0ae73ad1deb160ce046c3750851d0c3e94921ae3ef070eb8 |

Fonts in ../fonts/ (variable, latin and latin-ext subsets) come from the Fontsource packages
`@fontsource-variable/space-grotesk`, `@fontsource-variable/inter` and `@fontsource-variable/jetbrains-mono`
5.3.0, licensed under the SIL Open Font License 1.1 (LICENSE-*.txt next to them).

**Modification:** in `mediabunny-aac-encoder-1.64.0.min.mjs` the two imports `from"mediabunny"` are replaced by
`from"./mediabunny-1.64.0.min.mjs"`, because module workers cannot use import maps. Nothing else is changed; the
original file has SHA-256 39c27d4c51e61f1c19e26a85cd7a2b674be1d2cca059f5ddf2c1fa0430a4d0af. The encoder (libavcodec
compiled to WebAssembly) is loaded only in browsers without a native AAC encoder (e.g. Chrome on Linux, Firefox).
It needs `'wasm-unsafe-eval'` and `blob:` workers in the Content Security Policy.
