## Compiling

### MyGUI 3.4.3

As of 0.49.0 it needs MyGUI 3.4.3 and to be built with `-DMYGUI_DONT_USE_OBSOLETE=ON`.

```sh
wget "https://github.com/MyGUI/mygui/archive/refs/tags/MyGUI3.4.3.tar.gz"
tar -xvf MyGUI3.4.3.tar.gz

cd mygui-MyGUI3.4.3
mkdir build
cd build

cmake .. -DMYGUI_RENDERSYSTEM=1 -DMYGUI_BUILD_DEMOS=OFF -DMYGUI_BUILD_TOOLS=OFF -DMYGUI_BUILD_PLUGINS=OFF -DMYGUI_DONT_USE_OBSOLETE=ON

make -j4

sudo make install
```

### OpenSceneGraph OpenMW fork w/ bmdhacks astc support.

```sh

git clone https://github.com/OpenMW/osg.git

cd osg
mkdir build
cd build

# Fuck yeah. <_<
cmake .. \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
    -DCMAKE_DEBUG_POSTFIX="" -DCMAKE_MINSIZEREL_POSTFIX="" -DCMAKE_MINSIZEREL_POSTFIX="" -DCMAKE_RELEASE_POSTFIX="" -DCMAKE_RELWITHDEBINFO_POSTFIX="" \
     -DBUILD_OSG_PLUGINS_BY_DEFAULT=0 -DBUILD_OSG_PLUGIN_OSG=1 -DBUILD_OSG_PLUGIN_DAE=1 -DBUILD_OSG_PLUGIN_DDS=1 -DBUILD_OSG_PLUGIN_TGA=1 -DBUILD_OSG_PLUGIN_BMP=1 -DBUILD_OSG_PLUGIN_JPEG=1 -DBUILD_OSG_PLUGIN_PNG=1 -DBUILD_OSG_PLUGIN_FREETYPE=1 -DBUILD_OSG_DEPRECATED_SERIALIZERS=0 -DBUILD_OSG_PLUGIN_KTX=1 \
    -DOPENGL_PROFILE=GL2 \
    -DBUILD_EXAMPLES=OFF \
    -DASTCENC_INCLUDE_DIR="$PWD/../astc-encoder/Source" \
    -DASTCENC_LIBRARY="$PWD/../astc-encoder/build/Source/libastcenc-neon-shared.so"

make -j4

sudo make install

```

### OpenMW

```sh
## Use my fork.
git clone https://github.com/kloptops/openmw.git

cd openmw

# We're building 0.49.0 developer build, using my branch of `portmaster-fixes-0.49`
git checkout portmaster-fixes-0.49

mkdir build
cd build

export OSG_PATH="$PWD/../../osg/"

# This is mostly right i think, modify with `ccmake ..`
cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
    -DCMAKE_DEBUG_POSTFIX="" -DCMAKE_MINSIZEREL_POSTFIX="" -DCMAKE_MINSIZEREL_POSTFIX="" -DCMAKE_RELEASE_POSTFIX="" -DCMAKE_RELWITHDEBINFO_POSTFIX="" \
    -DBUILD_LAUNCHER=OFF -DBUILD_WIZARD=OFF -DBUILD_OPENCS=OFF -DBUILD_OPENCS_TESTS=OFF -DBUILD_ESSIMPORTER=OFF -DBUILD_BULLETOBJECTTOOL=OFF -DBUILD_MWINIIMPORTER=OFF -DBUILD_DOCS=OFF -DBUILD_ESMTOOL=OFF -DBUILD_BSATOOL=ON -DBUILD_NIFTEST=OFF -DBUILD_NAVMESHTOOL=ON \
    -DBOOST_STATIC=OFF -DOPENMW_GL4ES_MANUAL_INIT=OFF \
    \
    -DDYNAMIC_OPENSCENEGRAPH=ON \
    -DDYNAMIC_OPENTHREADS=OFF \
    -DOPENMW_USE_SYSTEM_BULLET=OFF \
    \
    -DMyGUI_LIBRARY=/usr/local/lib/aarch64-linux-gnu/libMyGUIEngine.so.3.4.3 \
    -DMyGUI_INCLUDE_DIR=/usr/local/include/MYGUI/ \
    -DOPENMW_USE_SYSTEM_MYGUI=ON \
    \
    -DOPENGL_gl_LIBRARY=~/Source/gl4es/lib/libGL.so.1 \
    -DOPENGL_INCLUDE_DIR=~/Source/gl4es/include/ \
    \
    -DOPENMW_USE_SYSTEM_OSG=ON \
    -DOpenSceneGraph_DIR="$OSG_PATH/build" \
    -DCMAKE_PREFIX_PATH="$OSG_PATH/build;$OSG_PATH" \
    -DCMAKE_LIBRARY_PATH="$OSG_PATH/build/lib" \
    -DCMAKE_INCLUDE_PATH="$OSG_PATH/include;$OSG_PATH/build/include"

# This is the working directory for ports.
ports_dir="/path/to/ports/directory/"

# After done copy the openmw and bsatool file to the port directory as openmw.aarch64, bsatool.aarch64
cp openmw $ports_dir/openmw.aarch64
cp bsatool $ports_dir/bsatool.aarch64

# Copy all the shared libs it needs... good luck. :D
## TODO: this is hard, so many libs from different sources.

# Next copy `resources/`
cp -r resources/ $ports_dir/openmw/

cd $ports_dir/openmw/

# Create un-modified shaders for SteamDeck
grep -F '+++ resources/' resources.GLES.patch | awk '{ print $2 };' | tar -cjf resources.OpenGL2.tar.bz2 --no-recursion -T -

# Apply patches to shaders for GLES.
patch -i resources.GLES.patch

# DONE!
```
