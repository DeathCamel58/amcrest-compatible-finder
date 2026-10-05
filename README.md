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

### Firmware we're still looking for
`wanted.json` lists firmware we've seen referenced (in datasets, forums, release notes, other vendors' listings) but
don't have yet. Every enrich run marks entries found once they're downloaded. Add a reference with
`python -m util.wanted add --name <file name> --source "<where it was seen>" [--url <link>]` (or `--version <version>
--model <model>` when only the version is known), and see what's missing with `python -m util.wanted status`.

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
 - [X] Dahua (dahuawiki.com, the official download center API across all regional sites, the China support portal's
   Chinese-language builds, files the old dahuasecurity.com product pages linked, and earlier builds the download
   center replaced but left on its file server, found from release-note version histories)

### Mirrors and archives
 - [ ] archive.cop-eu.com - **NOTE:** Gone
 - [X] ASM (ftp.asm.cz) - Czech Dahua distributor's file server (cameras, recorders, intercoms, access control)
 - [X] BITS (bits.com.ec) - Ecuadorian distributor's downloads page (its XiongMai builds are skipped)
 - [X] Cifra (ftp.cifra.cv.ua) - Ukrainian distributor's file server (a copy of Viatec's tree)
 - [X] Dahua France (france.dahuatech.com) - Dahua France's document centre: about 300 firmwares from 2015-2021 with MD5s and release notes
 - [X] Dahua India builds (indiafix.in) - Google Drive links to IndiaDahua_ recorder builds
 - [ ] drivers.softpedia.com - **NOTE:** Mirrors many Dahua recorder firmwares, but returns 403 to scripts
 - [X] Eltrox (ftp.eltrox.pl) - Polish distributor's file server, including the Kenik, EasyCam, Notis, Zeus, Irbis and
   Konig rebrands. Crawled slowly and cached, since it rate limits
 - [X] files.dahua.support - Dahua Poland's file server (firmware and solutions)
 - [X] files.dahuatech.support - Dahua support file server
 - [ ] ftp.tdtorus.ru - **NOTE:** Gone
 - [ ] ftp.wintel.fi - **NOTE:** Server is gone; only a list of file names survives on mmnt.net
 - [X] Global DS (Hungary) - distributor's public Google Drive folder, mostly CEE/Nordic intercom builds
 - [ ] i.botox.bz - **NOTE:** Gone
 - [X] ip-phone-forum.de - a thread collecting Dahua VTO/VTH firmware, on MediaFire
 - [X] ipcamtalk - a MediaFire folder and a Dropbox folder shared on the forum
 - [ ] pliki.dahuawiki.pl - **NOTE:** Same tree as files.dahua.support
 - [ ] previous.dahuasecurity.com - **NOTE:** Same catalog as the current Dahua download center, so nothing new
 - [X] Santronic (service.santronic.ch) - Swiss distributor's firmware page
 - [X] SecPoint (secpoint.it) - Italian distributor's open index (Dahua recorders, a Videotrend build, and Italian
   SIP/multipush intercom builds). Cloudflare only lets it through from EU addresses
 - [ ] share.dahuasecurity.com - **NOTE:** Gone
 - [ ] share.dahuaservice.it (Dahua Italy) - **NOTE:** Returns HTTP 500 (checked 2026-10-05); forum posts link intercom firmware there
 - [ ] smartsd.com - **NOTE:** Gone
 - [X] SNR (snr.systems) - Russian distributor's file server: Dahua recorders, cameras and intercoms from 2015-2018
 - [ ] support.dahuafrance.fr - **NOTE:** Gone (france.dahuatech.com is covered)
 - [X] Sveistrup (sveistrup.dk) - Danish distributor's firmware pages (about a fifth of its links are dead)
 - [X] TVC Mexico (soporte.tvc.mx) - listed from its forum's download page; the server has been down since
   2026-10-05, so only Wayback copies download
 - [X] Viatec (ftp.viatec.ua) - Ukrainian distributor's file server
 - [ ] Vidimost (vidimost.com) - **NOTE:** Ukrainian distributor with about 240 firmware pages, but downloads need a
   registered account; its public Wayback captures could be used
 - [X] Wayback Machine - recovers firmwares from dahuawiki, Dahua's file storage, Amcrest, Lorex, Rhino and others
   that are no longer served anywhere else
 - [X] Winco (ftp.winco.com.hk) - Hong Kong distributor's anonymous FTP

### Current
 - [ ] 2M CCTV - **NOTE:** Now Hikvision-based; downloads are apps/tools only
 - [ ] Activecam - **NOTE:** Site unreachable outside Russia, nothing archived
 - [ ] Advidia - **NOTE:** Hikvision firmware (digicap.dav); domain parked
 - [ ] Alibi - **NOTE:** Hikvision and Uniview firmware, not Dahua
 - [ ] Altoros - **NOTE:** Not a CCTV brand (software consultancy)
 - [X] Amcrest
 - [ ] Ameta - **NOTE:** Public Drive folders hold no firmware; support form only
 - [ ] Ascendent - **NOTE:** No site found
 - [ ] Backstreet Surveillance - **NOTE:** No firmware published (support desk only)
 - [X] Bosch - DIVAR AN/hybrid/network recorders (their cameras run Bosch's own firmware)
 - [ ] BV Security - **NOTE:** Hikvision and other platforms, not Dahua
 - [ ] CCTV Security Pros - **NOTE:** No firmware links; their Blue Line is GSS
 - [ ] CCTV Star - **NOTE:** Domains parked or for sale
 - [ ] ClareVision - **NOTE:** Hikvision firmware, not Dahua
 - [ ] Comelit - **NOTE:** TVT, Uniview and others, not Dahua
 - [X] CP Plus - Dahua-made recorders only (its UNR line isn't Dahua); live API is empty, so archived API answers and files are used
 - [ ] Dax Networks - **NOTE:** Networking company; only old network drivers
 - [X] DH Vision - recovered from the Wayback Machine
 - [X] Eastern CCTV / ENS Security - Diamond line (public Google Drive)
 - [ ] eLine - **NOTE:** Software only; firmware via support
 - [X] EmpireTech (Andy) - MEGA links; anonymous MEGA downloads have a transfer quota
 - [ ] Gess Technologies - **NOTE:** Domain now belongs to an unrelated company
 - [ ] Goliath / Avaloid - **NOTE:** Rebadged Dahua intercoms, but its firmware files are gone
 - [X] GSS
 - [ ] Heivision - **NOTE:** Distributor; no downloads
 - [ ] Hiltron - **NOTE:** Software only
 - [ ] Honeywell (a few of their product lines) - **NOTE:** Requires a Honeywell Discover login; line discontinued 2022
 - [ ] Hunt - **NOTE:** Hikvision firmware, not Dahua
 - [X] IC Realtime - public S3 bucket (the firmware page needs a login)
 - [ ] Ikegami - **NOTE:** No CCTV firmware published
 - [ ] Imou - **NOTE:** Dahua's consumer brand; firmware only updates through the app
 - [X] Inaxsys - STORM line (public SharePoint)
 - [ ] IndigoVision - **NOTE:** Firmware behind the Avigilon support login
 - [ ] Infinity CCTV - **NOTE:** Domain parked; only 2006 software archived
 - [ ] Innekt - **NOTE:** Domain hijacked; nothing archived
 - [X] Intelbras - behind Cloudflare; every product page and download goes through the browser (first run is slow, then cached)
 - [X] KBVision - listing only: downloads are private SharePoint links
 - [X] Lorex - **NOTE:** They require that you contact support to get firmware. Ref: https://help.lorextechnology.com/link/portal/57356/57366/Article/1451/Client-Software-Manually-updating-DVR-NVR-firmware. This currently uses the PDFs that I've found on their website
 - [ ] Luma - **NOTE:** Hikvision firmware, not Dahua
 - [ ] Lumixen - **NOTE:** Domain parked
 - [X] Lupus Electronics - German Dahua OEM (cameras and recorders)
 - [ ] Maxron - **NOTE:** Domain parked
 - [X] Montavue - Classic line only (Nexus is not Dahua)
 - [ ] Oco - **NOTE:** Cloud cameras; support by email only
 - [X] Optiview - legacy support server only
 - [ ] Panasonic (in certain countries only) - **NOTE:** Product pages only have manuals
 - [ ] People Fu - **NOTE:** Domain gone; only client software archived
 - [ ] Platinum CCTV - **NOTE:** Software only (own AVM VMS)
 - [ ] Qvis (UK) - **NOTE:** TVT firmware, not Dahua
 - [ ] RedSpeed - **NOTE:** Not a camera vendor (photo enforcement)
 - [X] Rhino Co - old 2013-2016 builds, plus their file library (`/file/display/{id}`)
 - [ ] Rhodium - **NOTE:** No site found; distributor only offers CMS software
 - [X] RVI - Dahua entries only, from the firmware page and the download ID library
 - [ ] SavvyTech - **NOTE:** No site found
 - [ ] Saxco - **NOTE:** Not a CCTV brand
 - [ ] Security Camera King - **NOTE:** Downloads are software only; firmware via support tickets
 - [ ] setik.biz - **NOTE:** Needs a registration
 - [ ] Space Technology - **NOTE:** No site found
 - [X] Speco - only a few of their recorders and cameras are Dahua (release notes and the WordPress media library)
 - [ ] ToughDog - **NOTE:** Support needs a customer ID
 - [ ] TSS Group - **NOTE:** firmware.tssgroup.support folders can't be listed
 - [ ] Unisight - **NOTE:** Only PDFs published
 - [X] VIP Vision
 - [ ] Visiotech - **NOTE:** Needs a login
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

