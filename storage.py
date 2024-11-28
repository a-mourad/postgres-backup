from abc import ABC, abstractmethod
import os
import boto3
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from minio import Minio
import io

class StorageProvider(ABC):
    @abstractmethod
    def upload_file(self, source_path, destination_path):
        pass

    @abstractmethod
    def list_files(self, prefix=""):
        pass

    @abstractmethod
    def delete_file(self, file_path):
        pass

    @abstractmethod
    def download_file(self, file_path, destination_path):
        pass

class LocalStorage(StorageProvider):
    def __init__(self, base_path):
        self.base_path = base_path

    def upload_file(self, source_path, destination_path):
        full_path = os.path.join(self.base_path, destination_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        if source_path != full_path:
            import shutil
            shutil.copy2(source_path, full_path)
        return full_path

    def list_files(self, prefix=""):
        full_path = os.path.join(self.base_path, prefix)
        if os.path.exists(full_path):
            files = [f for f in os.listdir(full_path) if os.path.isfile(os.path.join(full_path, f))]
            return [(f, os.path.join(full_path, f)) for f in files]
        return []

    def delete_file(self, file_path):
        full_path = os.path.join(self.base_path, file_path)
        if os.path.exists(full_path):
            os.remove(full_path)

    def download_file(self, file_path, destination_path):
        source_path = os.path.join(self.base_path, file_path)
        dest_dir = os.path.dirname(destination_path)
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
        import shutil
        shutil.copy2(source_path, destination_path)

class MinioStorage(StorageProvider):
    def __init__(self, endpoint, access_key, secret_key, bucket_name, secure=True):
        self.client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure
        )
        self.bucket_name = bucket_name
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self):
        if not self.client.bucket_exists(self.bucket_name):
            self.client.make_bucket(self.bucket_name)

    def upload_file(self, source_path, destination_path):
        self.client.fput_object(
            self.bucket_name,
            destination_path,
            source_path
        )
        return destination_path

    def list_files(self, prefix=""):
        objects = self.client.list_objects(self.bucket_name, prefix=prefix)
        return [(obj.object_name.split('/')[-1], obj.object_name) for obj in objects]
    def delete_file(self, file_path):
        self.client.remove_object(self.bucket_name, file_path)

    def download_file(self, file_path, destination_path):
        dest_dir = os.path.dirname(destination_path)
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
        self.client.fget_object(self.bucket_name, file_path, destination_path)

class GoogleDriveStorage(StorageProvider):
    def __init__(self, credentials_path, folder_id=None):
        credentials = Credentials.from_service_account_file(
            credentials_path,
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        self.service = build('drive', 'v3', credentials=credentials)
        self.folder_id = folder_id

    def upload_file(self, source_path, destination_path):
        # Google Drive upload is not implemented as it's not required for restore
        pass

    def list_files(self, prefix=""):
        query = f"name contains '{prefix}'" if prefix else None
        if self.folder_id:
            query = f"'{self.folder_id}' in parents" + (f" and {query}" if query else "")

        results = self.service.files().list(
            q=query,
            fields='files(id, name)',
            includeItemsFromAllDrives=True,
            supportsAllDrives=True
        ).execute()
        files = results.get('files', [])
        return [(file['name'], file['id']) for file in files]

    def delete_file(self, file_id):
        try:
            self.service.files().delete(fileId=file_id).execute()
        except Exception as e:
            raise Exception(f"Error deleting file from Google Drive: {str(e)}")

    def download_file(self, file_id, destination_path):
        dest_dir = os.path.dirname(destination_path)
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
        request = self.service.files().get_media(fileId=file_id)
        fh = io.FileIO(destination_path, mode='wb')
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"Downloaded {int(status.progress * 100)}%.")

class S3Storage(StorageProvider):
    def __init__(self, aws_access_key_id, aws_secret_access_key, bucket_name, region_name='us-east-1'):
        self.client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region_name
        )
        self.bucket_name = bucket_name

    def upload_file(self, source_path, destination_path):
        self.client.upload_file(source_path, self.bucket_name, destination_path)
        return destination_path

    def list_files(self, prefix=""):
        response = self.client.list_objects_v2(
            Bucket=self.bucket_name,
            Prefix=prefix
        )
        files = response.get('Contents', [])
        return [(obj['Key'].split('/')[-1], obj['Key']) for obj in files]

    def delete_file(self, file_path):
        self.client.delete_object(
            Bucket=self.bucket_name,
            Key=file_path
        )

    def download_file(self, file_path, destination_path):
        dest_dir = os.path.dirname(destination_path)
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
        self.client.download_file(self.bucket_name, file_path, destination_path)