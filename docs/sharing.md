# Sharing passwords with someone

Give a co-worker, assistant or designer the logins they need, without giving them your whole vault.

**How it works in one paragraph:** your own vault stays the master copy and keeps everything. For each person (or group) you make a small **shared vault**, a separate `.kdbx` file in a Google Drive folder only they can see. When you **share** a login with it, Vaultline keeps a copy there and keeps it up to date. One login can be shared with several people, for example a design tool with all three designers and a bank login with only one.

| Part | Who does it | How often |
|---|---|---|
| [1. Make a shared vault](#1-make-a-shared-vault-on-your-pc) | You | Once per person |
| [2. Set up their PC](#2-set-up-the-other-persons-pc) | You or them | Once per person |
| [3. Share logins](#3-share-logins-day-to-day) | You | Whenever you like |

---

## 1. Make a shared vault (on your PC)

1. **Make a Drive folder just for this person.** In File Explorer, under Google Drive (`G:`), make a folder such as `Shared with Sam`.
   > Nothing else goes in this folder, and **never your own vault file**. Whoever you share the folder with can see everything in it.
2. **Create the vault.** In Vaultline: **Settings → Shared vaults → Create a shared vault**. Save it in that folder, named after the person (`Sam.kdbx`). Keep the suggested name.
3. **That's it for the password.** Vaultline makes a long random password for the new vault and keeps it in your vault's **AutoOpen** folder. Your own master password opens both, so you never type it.
4. **Share the folder** (only that folder) with the person's Google account at **drive.google.com**: right-click the folder → **Share**.

---

## 2. Set up the other person's PC

They need Vaultline (or KeePassXC / KeePassium) and the shared vault's password, once.

1. **Copy the password.** In your vault, open the **AutoOpen** folder, open the entry with the vault's name, and copy its password.
   > Best: paste it on their PC yourself, so it's never sent anywhere. If you must send it, don't put the Drive link in the same message.
2. **Make the folder show up on their PC.** Folders shared with you appear online but **not in File Explorer** until you add a shortcut:
   go to **drive.google.com → Shared with me**, right-click the folder → **Organize → Add shortcut**, and add it to their own Drive. After a minute it appears under Google Drive in File Explorer.
3. **Open it in their Vaultline:** **Settings → Shared vaults → Open existing**. Pick the `.kdbx` in that folder and paste the password.
   > Leave **"I manage it" unticked** on their PC. Only your PC keeps the copies up to date.
4. Done. The shared vault now opens whenever they unlock their own vault, and shows as a folder in their list.

On a phone they can open the same file in KeePassium (iPhone) or KeePassDX (Android) with the same password.

---

## 3. Share logins (day to day)

| To… | Do this |
|---|---|
| Share one login | Right-click it → **Share with…** → tick one or more vaults |
| Share a whole folder | Point at the folder in the folder list → press the **share icon** → tick the vaults. Logins you add to it later are shared too |
| Stop sharing | **Share with…** again and untick. Their copy goes to their vault's recycle bin |
| See what someone has | Click **Shared with Sam** in your folder list |

**What happens after that:**

- **You change a shared login:** their copy updates by itself within seconds.
- **You delete it:** their copy is removed too.
- **They change or delete their copy:** nothing is overwritten. A bar appears in your vault, *"1 shared entry changed by someone else"*. Press **Review** and choose whose version to keep. It shows which fields changed, never the values.
- **They add a new login:** it's copied into your vault, in a folder named after their vault, so your vault really does have everything.

---

## Good to know

- **Only your PC syncs.** Vaultline has to be running and unlocked on your PC for changes to reach them. Changes made while it's closed go out the next time you unlock.
- **They see only what you share.** Their file holds nothing else, and it's encrypted with its own password.
- **Their Vaultline never syncs.** It just opens the shared vault. "I manage it" is ticked only on your PC.
- **A vault made before "I manage it" existed** (or opened with Open existing) can be switched over in **Settings → Shared vaults → I manage it**.
- **Stop sharing with someone entirely:** **Settings → Shared vaults → Stop opening**, then stop sharing the Drive folder. Your logins stay in your vault.

## For KeePassXC users

The link between the vaults is a standard KeePassXC **AutoOpen** entry, so KeePassXC opens the shared vault alongside yours too. Vaultline's sharing information is kept in KeePass "custom data", which other apps leave alone. Only Vaultline copies changes between the vaults.
