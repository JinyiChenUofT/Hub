import os
import numpy as np


BYTE_PADDING = b"\0"

# number of bytes per unit
B = 1
KB = 1000 * B
MB = 1000 * KB
GB = 1000 * MB

DEFAULT_HTYPE = "generic"

# used for requiring the user to specify a value for htype properties. notates that the htype property has no default.
REQUIRE_USER_SPECIFICATION = "require_user_specification"

# used for `REQUIRE_USER_SPECIFICATION` enforcement. this should be used instead of `None` for default user method arguments.
UNSPECIFIED = "unspecified"

SUPPORTED_MODES = ["r", "a"]

# min chunk size is always half of `DEFAULT_MAX_CHUNK_SIZE`
DEFAULT_MAX_CHUNK_SIZE = 32 * MB

MIN_FIRST_CACHE_SIZE = 32 * MB
MIN_SECOND_CACHE_SIZE = 160 * MB

# without MB multiplication, meant for the dataset API that takes cache size in MBs
DEFAULT_MEMORY_CACHE_SIZE = 256
DEFAULT_LOCAL_CACHE_SIZE = 0

# maximum allowable size before `large_ok` must be passed to dataset delete methods
DELETE_SAFETY_SIZE = 1 * GB

# meta is hub-defined information, necessary for hub Datasets/Tensors to function
DATASET_META_FILENAME = "dataset_meta.json"
TENSOR_META_FILENAME = "tensor_meta.json"

# info is user-defined information, entirely optional. may be used by the visualizer
DATASET_INFO_FILENAME = "dataset_info.json"
TENSOR_INFO_FILENAME = "tensor_info.json"

DATASET_LOCK_FILENAME = "dataset_lock.lock"
DATASET_DIFF_FILENAME = "dataset_diff"
TENSOR_COMMIT_CHUNK_SET_FILENAME = "chunk_set"
TENSOR_COMMIT_DIFF_FILENAME = "commit_diff"


DATASET_LOCK_UPDATE_INTERVAL = 120  # seconds
DATASET_LOCK_VALIDITY = 300  # seconds

META_ENCODING = "utf8"

CHUNKS_FOLDER = "chunks"
ENCODED_TILE_NAMES_FOLDER = "tiles_index"
ENCODED_CHUNK_NAMES_FOLDER = "chunks_index"
ENCODED_SEQUENCE_NAMES_FOLDER = "sequence_index"
# unsharded naming will help with backwards compatibility
UNSHARDED_ENCODER_FILENAME = "unsharded"

AGREEMENT_FILENAME = "agreement.txt"

ENCODING_DTYPE = np.uint32

# environment variables
ENV_HUB_DEV_USERNAME = "ACTIVELOOP_HUB_USERNAME"
ENV_HUB_DEV_PASSWORD = "ACTIVELOOP_HUB_PASSWORD"
ENV_KAGGLE_USERNAME = "KAGGLE_USERNAME"
ENV_KAGGLE_KEY = "KAGGLE_KEY"
ENV_GOOGLE_APPLICATION_CREDENTIALS = "GOOGLE_APPLICATION_CREDENTIALS"

HUB_CLOUD_DEV_USERNAME = os.getenv(ENV_HUB_DEV_USERNAME)
HUB_CLOUD_DEV_PASSWORD = os.getenv(ENV_HUB_DEV_PASSWORD)

# dataset base roots for pytests
PYTEST_MEMORY_PROVIDER_BASE_ROOT = "mem://hub_pytest"
PYTEST_LOCAL_PROVIDER_BASE_ROOT = "/tmp/hub_pytest/"  # TODO: may fail for windows
PYTEST_S3_PROVIDER_BASE_ROOT = "s3://hub-2.0-tests/"
PYTEST_GCS_PROVIDER_BASE_ROOT = "gcs://snark-test/"
PYTEST_HUB_CLOUD_PROVIDER_BASE_ROOT = (
    None if HUB_CLOUD_DEV_USERNAME is None else f"hub://{HUB_CLOUD_DEV_USERNAME}/"
)

# pytest options
MEMORY_OPT = "--memory-skip"
LOCAL_OPT = "--local"
S3_OPT = "--s3"
GCS_OPT = "--gcs"
GDRIVE_OPT = "--gdrive"
HUB_CLOUD_OPT = "--hub-cloud"
S3_PATH_OPT = "--s3-path"
KEEP_STORAGE_OPT = "--keep-storage"
KAGGLE_OPT = "--kaggle"


EMERGENCY_STORAGE_PATH = "/tmp/emergency_storage"
LOCAL_CACHE_PREFIX = "~/.activeloop/cache"

# used to identify the first commit so its data will not be in similar directory structure to the rest
FIRST_COMMIT_ID = "firstdbf9474d461a19e9333c2fd19b46115348f"
VERSION_CONTROL_INFO_FILENAME_OLD = "version_control_info"
VERSION_CONTROL_INFO_FILENAME = "version_control_info.json"
VERSION_CONTROL_INFO_LOCK_FILENAME = "version_control_info.lock"

# when cache is full upto this threshold, it will start suggesting new indexes intelligently based on existing contents
INTELLIGENT_SHUFFLING_THRESHOLD = 0.8

TRANSFORM_PROGRESSBAR_UPDATE_INTERVAL = 5  # seconds


# If True, and if the rest of the dataset is in color (3D), then reshape a grayscale image by appending a 1 to its shape.
CONVERT_GRAYSCALE = True

PARTIAL_NUM_SAMPLES = 0.5

QUERIES_FILENAME = "queries.json"
QUERIES_LOCK_FILENAME = "queries.lock"

_ENABLE_HUB_SUB_DATASETS = False
_ENABLE_RANDOM_ASSIGNMENT = False

# Frequency for sending progress events and writing to vds
QUERY_PROGRESS_UPDATE_FREQUENCY = 5  # seconds

PYTORCH_DATALOADER_TIMEOUT = 30  # seconds

_NO_LINK_UPDATE = "___!@#_no_link_update_###"

SAMPLE_INFO_TENSOR_MAX_CHUNK_SIZE = 4 * MB
