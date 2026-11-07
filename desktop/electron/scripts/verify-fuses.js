'use strict';
// Print the fuse wire of a built exe: node scripts/verify-fuses.js <path-to-ValoHub.exe>
const { getCurrentFuseWire, FuseV1Options, FuseState } = require('@electron/fuses');
const exe = process.argv[2];
if (!exe) { console.error('usage: verify-fuses.js <exe>'); process.exit(64); }
const expected = {
  RunAsNode: false, EnableCookieEncryption: true, EnableNodeOptionsEnvironmentVariable: false,
  EnableNodeCliInspectArguments: false, EnableEmbeddedAsarIntegrityValidation: true,
  OnlyLoadAppFromAsar: true, GrantFileProtocolExtraPrivileges: false,
};
getCurrentFuseWire(exe).then((wire) => {
  let bad = 0;
  for (const [name, want] of Object.entries(expected)) {
    const got = wire[FuseV1Options[name]];
    const ok = got === (want ? FuseState.ENABLE : FuseState.DISABLE);
    if (!ok) bad++;
    console.log(`${ok ? 'OK ' : 'BAD'} ${name} = ${got === FuseState.ENABLE ? 'ENABLED' : got === FuseState.DISABLE ? 'DISABLED' : got} (want ${want ? 'ENABLED' : 'DISABLED'})`);
  }
  console.log(JSON.stringify(wire));
  process.exit(bad ? 1 : 0);
}).catch((e) => { console.error(e.message); process.exit(2); });
