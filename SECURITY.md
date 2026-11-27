# Security policy

Vaultline holds people's passwords, so security reports are taken seriously and handled first.

## Reporting a vulnerability

**Please don't open a public issue for a security problem.** Report it privately instead:

1. Go to the repository's **Security** tab and click **Report a vulnerability**
   ([direct link](https://github.com/evega85/vaultline/security/advisories/new)).
2. Describe the problem, the Vaultline version (the release you downloaded), your Windows version, and the steps to reproduce it.

**Never include a real password, a real `.kdbx` file or a key file.** Use the demo vault (`demo/sample.kdbx`, password `demo123`) or a new test vault.

You should get a reply within a week. Fixes are released as a new version, and the advisory is published after the fix is out, with credit to you if you'd like.

## Supported versions

Only the latest release gets security fixes.

## In scope

- Anything that could expose a password, a one-time code secret, card details, notes or the master password: to disk, to the logs, to the clipboard for longer than intended, over the network, or through `vaultline-cli`.
- Anything that could corrupt or silently lose data in a `.kdbx` file, or make it unreadable by KeePassXC or KeePassium.
- Getting around the PIN lock, auto-lock, the "Share a folder with agents" approval prompt or its folder limit.
- Website-icon fetching that reaches private or local network addresses, or sends anything other than the site's own address.

## Out of scope

- A harmful program already running under your Windows account. It can read your screen, keyboard and clipboard, or change Vaultline's settings, and no desktop app can fully stop that.
- Someone with physical access to an unlocked PC.
- The strength of your master password, and the security of the cloud service you sync the file with.
- Windows SmartScreen warnings about the unsigned `.exe`.

## The security model in short

- **Your vault never leaves your PC.** Vaultline opens the `.kdbx` file where it is. It has no server, account or telemetry. The only network requests fetch website icons straight from each entry's own site (you can turn this off), and private or local addresses are skipped.
- **Standard encryption.** Files are read and written as standard KDBX 4 through [pykeepass](https://github.com/libkeepass/pykeepass), exactly as other KeePass apps expect.
- **The master password is never stored**, on disk or in the settings file. It is kept in memory only while Vaultline runs.
- **The PIN is stored only as a slow scrypt hash** in the settings file. A PIN lock drops the opened database from memory. Three wrong PINs or a restart means the master password is needed again.
- **Clipboard copies clear after 20 seconds** and are kept out of Windows clipboard history.
- **Saving can't leave a half-written file.** The file is re-read, merged and checked by decrypting it again, and a backup is kept. Only then is the file swapped in with one atomic rename.
- **`vaultline-cli` returns no secrets.** It is off by default and only works from your own Windows account, through a named pipe, while Vaultline is unlocked. The one exception is the opt-in **shared folder**. You pick one folder, and every request for a username, password or one-time code from it needs your approval in a prompt where **Deny** is the default (an approval can cover one entry for 15 minutes, or the whole folder for a time you choose in Settings; any lock ends it). Each request is written to an access log, which never holds the value. Locking cancels every allowance.
