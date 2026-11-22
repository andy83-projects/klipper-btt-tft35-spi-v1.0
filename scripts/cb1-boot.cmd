# Armbian boot script, hardened for the BIQU B1 SE Plus CB1.
#
# U-Boot SD reads on this board fail intermittently, and after one failure
# the card stays unreadable until "mmc rescan". Every file access below
# rescans and retries, and the board never boots with a partially loaded
# environment, device tree, initrd or kernel: on any unreadable file the
# script returns so cb1_retry_boot (U-Boot environment) tries again.
#
# Original Armbian script: /boot/boot.cmd.armbian-orig
# Recompile with:
# mkimage -C none -A arm -T script -d /boot/boot.cmd /boot/boot.scr

# default values
setenv load_addr "0x45000000"
setenv overlay_error "false"
setenv rootdev "/dev/mmcblk0p1"
setenv verbosity "1"
setenv rootfstype "ext4"
setenv console "both"
setenv docker_optimizations "on"
setenv bootlogo "false"

setenv vendor "allwinner"

# Rescan + load ${cb1_lf} to ${cb1_la}; up to 20 tries. Status: loaded or not.
setenv cb1_load 'setenv cb1_ok; for cb1_try in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do if test -z "${cb1_ok}"; then mmc rescan; if load ${devtype} ${devnum} ${cb1_la} ${cb1_lf}; then setenv cb1_ok 1; else echo "Retrying ${cb1_lf} (${cb1_try})"; fi; fi; done; test -n "${cb1_ok}"'
# Rescan + check that optional ${cb1_lf} exists; up to 3 tries.
setenv cb1_exists 'setenv cb1_ok; for cb1_try in 1 2 3; do if test -z "${cb1_ok}"; then mmc rescan; if test -e ${devtype} ${devnum} ${cb1_lf}; then setenv cb1_ok 1; fi; fi; done; test -n "${cb1_ok}"'
setenv cb1_fail

# Remember the default fdtfile provided by u-boot and delete the vendor name
if setexpr subfdt sub ${vendor}/ "" ${fdtfile};then
	setenv deffdt_file ${subfdt}
fi

# Remember the default u-boot fdtdir
setenv deffdt_dir "${prefix}dtb"
if test "$fdtdir" = ""; then setenv fdtdir "${deffdt_dir}/${vendor}";fi

# Print boot source
itest.b *0x10028 == 0x00 && echo "U-boot loaded from SD"
itest.b *0x10028 == 0x02 && echo "U-boot loaded from eMMC or secondary SD"
itest.b *0x10028 == 0x03 && echo "U-boot loaded from SPI"

echo "Boot script loaded from ${devtype} (CB1 retrying loader)"

# armbianEnv.txt is required: without it rootdev would fall back to the
# wrong default and the kernel would wait forever for a missing root.
setenv cb1_la ${load_addr}
setenv cb1_lf ${prefix}armbianEnv.txt
if run cb1_load; then
	env import -t ${load_addr} ${filesize}
else
	setenv cb1_fail "armbianEnv.txt"
fi

# Delete the vendor's name from the fdtfile variable and record the result
# after the file with the environment variables has been read
if setexpr subfdt sub ${vendor}/ "" ${fdtfile};then
	setenv fdtfile ${subfdt}
fi

if test "${console}" = "detail"; then setenv consoleargs "console=tty0 console=ttyS0,115200"; fi
if test "${console}" = "display" || test "${console}" = "both"; then setenv consoleargs "console=ttyS0,115200 console=tty1"; fi
if test "${console}" = "serial"; then setenv consoleargs "console=tty1"; fi
if test "${console}" = "cancel_lcd"; then setenv consoleargs "console=ttyS0,115200"; fi
if test "${bootlogo}" = "true"; then
	setenv consoleargs "splash plymouth.ignore-serial-consoles ${consoleargs}"
else
	setenv consoleargs "splash=verbose ${consoleargs}"
fi

# get PARTUUID of first partition on SD/eMMC it was loaded from
# mmc 0 is always mapped to device u-boot (2016.09+) was loaded from
if test "${devtype}" = "mmc"; then
	setenv partuuid
	for cb1_try in 1 2 3 4 5; do
		if test -z "${partuuid}"; then mmc rescan; part uuid mmc 0:1 partuuid; fi
	done
fi

setenv bootargs "root=${rootdev} rootwait rootfstype=${rootfstype} ${consoleargs} consoleblank=0 loglevel=${verbosity} ubootpart=${partuuid} usb-storage.quirks=${usbstoragequirks} ${extraargs} ${extraboardargs}"

if test "${docker_optimizations}" = "on"; then setenv bootargs "${bootargs} cgroup_enable=memory"; fi

# Device tree: configured directory first, then the default directory.
if test -z "${cb1_fail}"; then
	setenv cb1_la ${fdt_addr_r}
	setenv cb1_lf ${fdtdir}/${fdtfile}
	if run cb1_load; then
		echo "Load fdt: ${cb1_lf}"
	else
		setenv cb1_lf ${deffdt_dir}/${fdtfile}
		if run cb1_load; then
			setenv fdtdir "${deffdt_dir}"
			echo "Load fdt: ${cb1_lf}"
		else
			setenv cb1_fail "${fdtfile}"
		fi
	fi
fi

if test -z "${cb1_fail}"; then
	fdt addr ${fdt_addr_r}
	fdt resize 65536
	for overlay_file in ${overlays}; do
		setenv cb1_la ${load_addr}
		setenv cb1_lf ${fdtdir}/overlay/${overlay_prefix}-${overlay_file}.dtbo
		if run cb1_load; then
			echo "Applying kernel provided DT overlay ${overlay_prefix}-${overlay_file}.dtbo"
			fdt apply ${load_addr} || setenv overlay_error "true"
		else
			setenv cb1_fail "${cb1_lf}"
		fi
	done
	for overlay_file in ${user_overlays}; do
		setenv cb1_la ${load_addr}
		setenv cb1_lf ${prefix}overlay-user/${overlay_file}.dtbo
		if run cb1_load; then
			echo "Applying user provided DT overlay ${overlay_file}.dtbo"
			fdt apply ${load_addr} || setenv overlay_error "true"
		else
			setenv cb1_fail "${cb1_lf}"
		fi
	done
fi

if test -z "${cb1_fail}"; then
	if test "${overlay_error}" = "true"; then
		echo "Error applying DT overlays, restoring original DT"
		setenv cb1_la ${fdt_addr_r}
		setenv cb1_lf ${fdtdir}/${fdtfile}
		if run cb1_load; then fdt addr ${fdt_addr_r}; else setenv cb1_fail "${fdtfile}"; fi
	else
		setenv cb1_lf ${fdtdir}/overlay/${overlay_prefix}-fixup.scr
		if test -n "${overlays}${user_overlays}" && run cb1_exists; then
			setenv cb1_la ${load_addr}
			if run cb1_load; then
				echo "Applying kernel provided DT fixup script (${overlay_prefix}-fixup.scr)"
				source ${load_addr}
			else
				setenv cb1_fail "${cb1_lf}"
			fi
		fi
		setenv cb1_lf ${prefix}fixup.scr
		if run cb1_exists; then
			setenv cb1_la ${load_addr}
			if run cb1_load; then
				echo "Applying user provided fixup script (fixup.scr)"
				source ${load_addr}
			else
				setenv cb1_fail "${cb1_lf}"
			fi
		fi
	fi
fi

if test -z "${cb1_fail}"; then
	setenv cb1_la ${ramdisk_addr_r}
	setenv cb1_lf ${prefix}uInitrd
	if run cb1_load; then
		setenv cb1_la ${kernel_addr_r}
		setenv cb1_lf ${prefix}Image
		if run cb1_load; then
			booti ${kernel_addr_r} ${ramdisk_addr_r} ${fdt_addr_r}
			setenv cb1_fail "booti"
		else
			setenv cb1_fail "Image"
		fi
	else
		setenv cb1_fail "uInitrd"
	fi
fi

echo "CB1 boot script stopped: could not use ${cb1_fail}; retrying"
