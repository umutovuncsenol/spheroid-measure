#!/usr/bin/env bash
source "/Users/elifduman/miniforge3/etc/profile.d/conda.sh"
conda activate sferoid
export KMP_DUPLICATE_LIB_OK=TRUE
export OMP_NUM_THREADS=1
cd "/Users/elifduman/Desktop/spheroid-measure"
exec python -m app
