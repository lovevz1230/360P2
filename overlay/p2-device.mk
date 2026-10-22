
define Device/360_p2
  IMAGE_SIZE := 16064k
  DEVICE_VENDOR := 360
  DEVICE_MODEL := P2
  DEVICE_PACKAGES := kmod-mt76x2 kmod-usb2 kmod-usb-ohci \
	kmod-usb-ledtrig-usbport kmod-usb-storage block-mount kmod-fs-ext4
endef
TARGET_DEVICES += 360_p2
