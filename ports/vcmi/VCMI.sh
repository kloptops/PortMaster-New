#!/bin/bash

XDG_DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}

if [ -d "/opt/system/Tools/PortMaster/" ]; then
  controlfolder="/opt/system/Tools/PortMaster"
elif [ -d "/opt/tools/PortMaster/" ]; then
  controlfolder="/opt/tools/PortMaster"
elif [ -d "$XDG_DATA_HOME/PortMaster/" ]; then
  controlfolder="$XDG_DATA_HOME/PortMaster"
else
  controlfolder="/roms/ports/PortMaster"
fi

source $controlfolder/control.txt
get_controls

## TODO: Change to PortMaster/tty when Johnnyonflame merges the changes in,
CUR_TTY="${CUR_TTY:/dev/tty0}"

PORTDIR="/$directory/ports"
GAMEDIR="$PORTDIR/vcmi"
> "$GAMEDIR/log.txt" && exec > >(tee "$GAMEDIR/log.txt") 2>&1

cd $GAMEDIR

$ESUDO chmod 666 $CUR_TTY
$ESUDO touch log.txt
$ESUDO chmod 666 log.txt
export TERM=linux
printf "\033c" > $CUR_TTY

printf "\033c" > $CUR_TTY
## RUN SCRIPT HERE

export DEVICE_ARCH="$DEVICE_ARCH"

if [[ ! -d "${GAMEDIR}/data/" ]]; then
  FILES_TO_REMOVE=()
  BUILDER_OPTIONS=()
  if [ -f setup_heroes_of_might_and_magic_3_*.exe ]; then
    # Install from gog installer
    FILES_TO_REMOVE+=(setup_heroes_of_might_and_magic_3_*.exe setup_heroes_of_might_and_magic_3_*.bin)
    BUILDER_OPTIONS+=("--gog" setup_heroes_of_might_and_magic_3_*.exe)
  elif [ -d "${GAMEDIR}/cd1" ] && [ -d "${GAMEDIR}/cd2" ]; then
    BUILDER_OPTIONS+=("--cd1" "${GAMEDIR}/cd1" "--cd2" "${GAMEDIR}/cd2")
    FILES_TO_REMOVE+=("${GAMEDIR}/cd1" "${GAMEDIR}/cd2")
  elif [ -d "${GAMEDIR}/install" ]; then
    BUILDER_OPTIONS+=("--data" "${GAMEDIR}/install")
    FILES_TO_REMOVE+=("${GAMEDIR}/install")
  else
    echo "Missing game files, see README for more info." > $CUR_TTY
    sleep 5
    printf "\033c" > $CUR_TTY
    $ESUDO systemctl restart oga_events &
    exit 1
  fi

  LD_LIBRARY_PATH="${PWD}/libs.${DEVICE_ARCH}:$LD_LIBRARY_PATH" bin/vcmibuilder --dest "${PWD}/data/" ${BUILDER_OPTIONS[@]}
  $ESUDO rm -fRv ${FILES_TO_REMOVE[@]}
  cd $GAMEDIR
fi

echo "Starting game." > $CUR_TTY

export PORTMASTER_HOME="${GAMEDIR}"
export LD_LIBRARY_PATH="${GAMEDIR}/libs.${DEVICE_ARCH}:${LD_LIBRARY_PATH}"
export SDL_GAMECONTROLLERCONFIG="$sdl_controllerconfig"
$ESUDO chmod 666 /dev/uinput

$GPTOKEYB "vcmiclient.${DEVICE_ARCH}" &
./bin/vcmiclient.${DEVICE_ARCH} 2>&1 | $ESUDO tee -a ./log.txt

$ESUDO kill -9 $(pidof gptokeyb)
$ESUDO killall -9 tee

$ESUDO systemctl restart oga_events &

# Disable console
printf "\033c" > $CUR_TTY

