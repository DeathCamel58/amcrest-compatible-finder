# Amcrest Compatible Finder
This tool downloads all firmwares across various vendors, and determines which firmwares are compatible with which Dahua devices.

## Setup
```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/scrapling install   # browser used to get past Cloudflare (Amcrest, GSS)
.venv/bin/python main.py
```
To refuse commits of `cameras.json`, `firmware_compatible.json` or `data/` that don't pass validation, enable the
pre-commit hook once per clone: `git config core.hooksPath scripts/hooks`

`binwalk`, `unzip` and `file` need to be installed. Firmwares are large (hundreds of GB across all vendors), so
`firmware/` can be a symlink to another drive.

## Output
 - `cameras.json`: firmware file name -> where it came from (vendors, model names, URL, version, release date, hashes)
 - `firmware_compatible.json`: an index of firmware file name -> hardware IDs found inside the firmware, analysis status,
   SoC, and the path of its detail file
 - `data/firmware/`: one detail file per firmware (where each hardware ID came from, SoC, security, versions,
   partitions), named by content hash; `data/layouts/`: each distinct partition layout once

See `docs/` for example output and the field reference.

Every downloaded firmware is also uploaded to the Internet Archive (needs `ia configure` once).

### Running some stages only
`python main.py` runs every stage: download, enrich, process, archive and validate. Name stages to run only those
(they always run in pipeline order), or use a group or range:
```
python main.py archive                      # upload to archive.org only
python main.py download process             # download, then process
python main.py analyse                      # enrich, process, validate: no network
python main.py --no-archive                 # the full run without uploading (same as: python main.py update)
python main.py --from process               # process, archive, validate
python main.py check-links                  # check whether vendor links still work (not part of a full run)
python main.py download --only EmpireTech   # one source (--skip leaves sources out; --list-sources names them)
```
`python main.py --help` lists every stage and option.

## OEM Support
 - [X] Dahua (dahuawiki.com and the official download center API across all regional sites)

### Mirrors and archives
 - [X] ASM (ftp.asm.cz) - Czech Dahua distributor's file server (cameras, recorders, intercoms, access control)
 - [X] files.dahuatech.support - Dahua support file server
 - [X] files.dahua.support - Dahua Poland's file server (firmware and solutions)
 - [X] Dahua France (france.dahuatech.com) - Dahua France's document centre: about 300 firmwares from 2015-2021 with MD5s and release notes
 - [X] Eltrox (ftp.eltrox.pl) - Polish distributor's file server, including the Kenik, EasyCam, Notis, Zeus, Irbis and
   Konig rebrands. Crawled slowly and cached, since it rate limits
 - [X] Viatec (ftp.viatec.ua) - Ukrainian distributor's file server
 - [X] Cifra (ftp.cifra.cv.ua) - Ukrainian distributor's file server (a copy of Viatec's tree)
 - [X] Wayback Machine - recovers firmwares from dahuawiki, Dahua's file storage, Amcrest, Lorex, Rhino and others
   that are no longer served anywhere else
 - [ ] ftp.wintel.fi - **NOTE:** Server is gone; only a list of file names survives on mmnt.net
 - [ ] previous.dahuasecurity.com - **NOTE:** Same catalog as the current Dahua download center, so nothing new

### Current
 - [ ] 2M CCTV - **NOTE:** Now Hikvision-based; downloads are apps/tools only
 - [ ] Activecam - **NOTE:** Site unreachable outside Russia, nothing archived
 - [ ] Advidia - **NOTE:** Hikvision firmware (digicap.dav); domain parked
 - [ ] Altoros - **NOTE:** Not a CCTV brand (software consultancy)
 - [X] Amcrest
 - [ ] Ameta - **NOTE:** Public Drive folders hold no firmware; support form only
 - [ ] Ascendent - **NOTE:** No site found
 - [ ] Backstreet Surveillance - **NOTE:** No firmware published (support desk only)
 - [X] Bosch - DIVAR AN/hybrid/network recorders (their cameras run Bosch's own firmware)
 - [ ] BV Security - **NOTE:** Hikvision and other platforms, not Dahua
 - [ ] CCTV Security Pros - **NOTE:** No firmware links; their Blue Line is GSS
 - [ ] CCTV Star - **NOTE:** Domains parked or for sale
 - [X] CP Plus - Dahua-made recorders only; live API is empty, so archived API answers and files are used
 - [ ] Dax Networks - **NOTE:** Networking company; only old network drivers
 - [X] DH Vision - recovered from the Wayback Machine
 - [ ] eLine - **NOTE:** Software only; firmware via support
 - [X] Eastern CCTV / ENS Security - Diamond line (public Google Drive)
 - [X] EmpireTech (Andy) - MEGA links; anonymous MEGA downloads have a transfer quota
 - [X] Lorex - **NOTE:** They require that you contact support to get firmware. Ref: https://help.lorextechnology.com/link/portal/57356/57366/Article/1451/Client-Software-Manually-updating-DVR-NVR-firmware. This currently uses the PDFs that I've found on their website
 - [ ] Gess Technologies - **NOTE:** Domain now belongs to an unrelated company
 - [X] GSS
 - [ ] Honeywell (a few of their product lines) - **NOTE:** Requires a Honeywell Discover login; line discontinued 2022
 - [ ] Heivision - **NOTE:** Distributor; no downloads
 - [X] IC Realtime - public S3 bucket (the firmware page needs a login)
 - [ ] Ikegami - **NOTE:** No CCTV firmware published
 - [X] Inaxsys - STORM line (public SharePoint)
 - [ ] IndigoVision - **NOTE:** Firmware behind the Avigilon support login
 - [ ] Infinity CCTV - **NOTE:** Domain parked; only 2006 software archived
 - [ ] Innekt - **NOTE:** Domain hijacked; nothing archived
 - [X] Intelbras - behind Cloudflare; every product page and download goes through the browser (first run is slow, then cached)
 - [X] KBVision - listing only: downloads are private SharePoint links
 - [ ] Lumixen - **NOTE:** Domain parked
 - [ ] Maxron - **NOTE:** Domain parked
 - [X] Montavue - Classic line only (Nexus is not Dahua)
 - [ ] Oco - **NOTE:** Cloud cameras; support by email only
 - [X] Optiview - legacy support server only
 - [ ] Panasonic (in certain countries only) - **NOTE:** Product pages only have manuals
 - [ ] People Fu - **NOTE:** Domain gone; only client software archived
 - [ ] Platinum CCTV - **NOTE:** Software only (own AVM VMS)
 - [ ] RedSpeed - **NOTE:** Not a camera vendor (photo enforcement)
 - [X] Rhino Co - old 2013-2016 builds, plus their file library (`/file/display/{id}`)
 - [ ] Rhodium - **NOTE:** No site found; distributor only offers CMS software
 - [X] RVI - Dahua entries only, from the firmware page and the download ID library
 - [ ] Saxco - **NOTE:** Not a CCTV brand
 - [ ] SavvyTech - **NOTE:** No site found
 - [ ] Security Camera King - **NOTE:** Downloads are software only; firmware via support tickets
 - [ ] Space Technology - **NOTE:** No site found
 - [X] Speco - only a few of their recorders and cameras are Dahua (release notes and the WordPress media library)
 - [ ] ToughDog - **NOTE:** Support needs a customer ID
 - [ ] Unisight - **NOTE:** Only PDFs published
 - [X] VIP Vision
 - [ ] Watchnet - **NOTE:** No firmware in their downloads
 - [X] Winic - Dahua lines only (the rest is Hikvision)
 - [X] Zuum - public Dropbox folder

### Former
 - [ ] ADT (their residential line used to be Dahua OEM) - **NOTE:** No public firmware, nothing archived
 - [ ] Annke (move to Hikvision OEMs)
 - [ ] Aposonic - **NOTE:** Nothing archived
 - [ ] BCS - **NOTE:** No Dahua firmware found, nothing archived
 - [ ] Cantek - **NOTE:** No site found
 - [ ] Dotix - **NOTE:** No site found
 - [ ] DVR Unlimited - **NOTE:** Nothing archived
 - [ ] Eyenor - **NOTE:** Nothing usable archived
 - [ ] FLIR - **NOTE:** Archived files are Digimerge DVR firmware, not Dahua; current support needs a login
 - [ ] HQVision - **NOTE:** Nothing archived
 - [ ] Legrand - **NOTE:** No site found
 - [ ] Norden - **NOTE:** No site found
 - [ ] Q-See - **NOTE:** Archived files are viewer software only; the new q-see.com isn't Dahua
 - [ ] Raster - **NOTE:** No site found
 - [ ] Riva - **NOTE:** No site found
 - [X] SecurityTronix - legacy HD-CVI recorders (the current line is Hikvision)
 - [ ] Techpro - **NOTE:** Same company as Security Camera King; software only
 - [ ] Tyco Holis - **NOTE:** No site found, nothing archived
 - [ ] Tyco Illustra EssentialsUra - **NOTE:** Illustra Essentials: nothing public or archived. Ura: no site found
 - [ ] Watashi - **NOTE:** Domain redirects to an unrelated site; nothing archived

