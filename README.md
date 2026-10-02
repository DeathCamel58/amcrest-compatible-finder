# Amcrest Compatible Finder
This tool downloads all firmwares across various vendors, and determines which firmwares are compatible with which Dahua devices.

## Setup
```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/scrapling install   # browser used to get past Cloudflare (Amcrest, GSS)
.venv/bin/python main.py
```
`binwalk`, `unzip` and `file` need to be installed. Firmwares are large (hundreds of GB across all vendors), so
`firmware/` can be a symlink to another drive.

## Output
 - `cameras.json`: firmware file name -> where it came from (vendors, model names, URL, version, release date, hashes)
 - `firmware_compatible.json`: firmware file name -> hardware IDs found inside the firmware

See `docs/` for example output and the field reference.

Every downloaded firmware is also uploaded to the Internet Archive (needs `ia configure` once). Run a single step
with `python main.py download`, `python main.py process` or `python main.py archive`.

## OEM Support
 - [X] Dahua (dahuawiki.com and the official download center API across all regional sites)

### Current
 - [ ] 2M CCTV
 - [ ] Activecam - **NOTE:** Site unreachable from the US
 - [ ] Advidia
 - [ ] Altoros
 - [X] Amcrest
 - [ ] Ameta - **NOTE:** They require that you contact support to get firmware. Ref: https://www.ametagroup.com/firmware
 - [ ] Ascendent
 - [ ] Backstreet Surveillance - **NOTE:** Sucuri bot challenge
 - [ ] Bosch (a few of their cameras and NVR models)
 - [ ] BV Security
 - [ ] CCTV Security Pros - **NOTE:** No firmware links; their Blue Line is GSS
 - [ ] CCTV Star
 - [ ] CP Plus - **NOTE:** Firmware API returns no data; files only recoverable via the Wayback Machine
 - [ ] Dax Networks
 - [ ] DH Vision
 - [ ] eLine
 - [X] Eastern CCTV / ENS Security - Diamond line (public Google Drive)
 - [X] EmpireTech (Andy) - MEGA links; anonymous MEGA downloads have a transfer quota
 - [X] Lorex - **NOTE:** They require that you contact support to get firmware. Ref: https://help.lorextechnology.com/link/portal/57356/57366/Article/1451/Client-Software-Manually-updating-DVR-NVR-firmware. This currently uses the PDFs that I've found on their website
 - [ ] Gess Technologies
 - [X] GSS
 - [ ] Honeywell (a few of their product lines) - **NOTE:** Requires a Honeywell login; line discontinued
 - [ ] Heivision
 - [ ] IC Realtime - **NOTE:** Dealer login required
 - [ ] Ikegami
 - [X] Inaxsys - STORM line (public SharePoint)
 - [ ] IndigoVision
 - [ ] Infinity CCTV
 - [ ] Innekt
 - [ ] Intelbras - **NOTE:** Cloudflare on every page
 - [ ] KBVision - **NOTE:** Downloads are private SharePoint links
 - [ ] Lumixen
 - [ ] Maxron
 - [X] Montavue - Classic line only (Nexus is not Dahua)
 - [ ] Oco
 - [X] Optiview - legacy support server only
 - [ ] Panasonic (in certain countries only)
 - [ ] People Fu
 - [ ] Platinum CCTV - **NOTE:** Cloudflare, no Dahua evidence
 - [ ] RedSpeed
 - [X] Rhino Co - old 2013-2016 builds
 - [ ] Rhodium
 - [X] RVI - Dahua entries only
 - [ ] Saxco
 - [ ] SavvyTech
 - [ ] Security Camera King
 - [ ] Space Technology
 - [X] Speco - only a couple of their recorders are Dahua
 - [ ] ToughDog
 - [ ] Unisight
 - [X] VIP Vision
 - [ ] Watchnet
 - [ ] Winic
 - [ ] Zuum

### Former
 - [ ] ADT (their residential line used to be Dahua OEM)
 - [ ] Annke (move to Hikvision OEMs)
 - [ ] Aposonic
 - [ ] BCS
 - [ ] Cantek
 - [ ] Dotix
 - [ ] DVR Unlimited
 - [ ] Eyenor
 - [ ] FLIR
 - [ ] HQVision
 - [ ] Legrand
 - [ ] Norden
 - [ ] Q-See
 - [ ] Raster
 - [ ] Riva
 - [ ] SecurityTronix
 - [ ] Techpro
 - [ ] Tyco Holis
 - [ ] Tyco Illustra EssentialsUra
 - [ ] Watashi

