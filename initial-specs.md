# Project Brief: Automated Outdoor Camera & Daily Bluesky Bot

## 1. Objective & Business Goal
I want to build a hands-off, automated outdoor photography system installed in a sheltered area outside my house. The system will capture scheduled high-resolution photos throughout the day (around a dozen per day), archive the full-resolution images to the cloud, and automatically publish one daily snapshot to my Bluesky account.

## 2. Hardware Setup
* **Controller:** Raspberry Pi Zero (chosen for low power draw and compact footprint, powered by a standard 5V 1.5A–2A supply and connected to the camera via a micro-USB OTG adapter).
* **Camera:** Industrial 12MP USB UVC camera (Sony IMX362 1/2.55" sensor, USB 2.0 plug-and-play, 5V / 160mA–260mA power draw).
* **Imaging Capabilities:**
  * Captures up to **4000×3000 (12MP)** still images in MJPEG format (no raw sensor output).
  * Supports software-adjustable focus, exposure, white balance, gain, and anti-flicker settings—allowing focus to be locked at infinity for distant outdoor scenery so it does not hunt or focus on nearby obstructions.

## 3. Expected Solution
* **Automated Capture & Calibration:** A lightweight background service running on the Raspberry Pi Zero that overrides default auto-settings to maintain a locked infinity focus, allows outdoor lighting to stabilize before capturing, and saves ~12 full-resolution 12MP photos per day.
* **Cloud Backup:** Automatic syncing or uploading of the full-resolution 12MP photo archive to cloud storage.
* **Daily Bluesky Publishing:** An automated daily job that authenticates via a Bluesky App Password, optimizes/resizes the selected photo to meet Bluesky's upload file-size limit (2 MB max per image) without noticeable quality loss, and posts it with descriptive alt text.
* **Reliability:** Resilient operation across power cycles and network interruptions, ensuring camera settings are reapplied automatically on reboot and failed uploads are retried cleanly.