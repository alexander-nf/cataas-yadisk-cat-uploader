import logging
from dataclasses import dataclass
from json import dumps, loads
from os import getenv
from urllib.parse import quote, unquote

import requests
from dotenv import load_dotenv
from pathvalidate import sanitize_filename


@dataclass(frozen=True)
class Settings:
    '''Глобальные настройки, не меняющиеся во время исполнения программы.'''
    cataas_api_url: str
    ya_disk_api_url: str
    ya_disk_token: str
    netology_group_id: str    
    http_timeout: int
    app_logging_level: str

    def __str__(self) -> str:
        return (
            f'URL CatAAS:                   {self.cataas_api_url}\n'
            f'URL API Я.Диска:              {self.ya_disk_api_url}\n'
            #f'Токен Я.Диска:                {self.ya_disk_token}\n'
            f'Каталог на Я.Диске (№ группы) {self.netology_group_id}\n'
            f'Таймаут HTTP-запроса          {self.http_timeout} секунд\n'
            f'Уровень детализации лога:     {self.app_logging_level}'
        )

    @classmethod
    def load_env_settings(cls) -> 'Settings':
        load_dotenv()
        return cls(
            netology_group_id=getenv('NETOLOGY_GROUP_ID'),
            ya_disk_api_url=getenv('YA_DISK_API_URL'),
            ya_disk_token=getenv('YA_DISK_TOKEN'),
            cataas_api_url=getenv('CATAAS_API_URL'),
            http_timeout=int(getenv('HTTP_TIMEOUT_SECS')),
            app_logging_level=getenv('APP_LOGGING_LEVEL', 'DEBUG')
        )


@dataclass
class Image:
    '''Картинка с котиком, её метаданные и методы их получения'''
    text: str
    mime_type: str
    data: bytes

    def __str__(self) -> str:
        return (
            f'"{self.name}.{self.extension}": '
            f'надпись "{self.text}", '            
            f'размер: {self.size} байт'
        )

    @property
    def info(self) -> dict:
        return {
            'file_name': f'{self.name}.{self.extension}',
            'size': self.size,
            'text': self.text
        }

    @property
    def name(self) -> str:
        if self.text:
            return sanitize_filename(self.text)
        else:
            return 'noname_cat'

    @property
    def extension(self) -> str:
        return self.mime_type.partition('/')[2]

    @property
    def size(self) -> int:
        return len(self.data)


class CataasClient:
    '''Модуль взаимодействия с CatAAS'''
    def __init__(self, api_url: str):
        self.base = api_url

    def get_cat_image(self, text='') -> Image | None:
        if text:
            text = quote(text, safe='')  # кодируем текст для URL и убираем "опасные" символы
            image_url = f'{self.base}/says/{text}'
        else:
            image_url = self.base

        logger.info(
            f'Запрос изображения у CatAAS, '
            f'надпись {unquote(text) or "не задана"}'
        )
        logger.debug(f'Обращение к CatAAS API по адресу {image_url}')
        
        try:
            response = requests.get(image_url, timeout=settings.http_timeout)
        except (requests.RequestException, ValueError) as error:
            logger.error(f'Не удалось получить изображение: {error}')
            return None
        else:
            content_type = response.headers.get('Content-Type', '').partition(';')[0] 
            if not content_type.startswith('image/'):
                raise ValueError(
                    f'Сервер вернул не изображение, '
                    f'а {content_type or "неопознанный тип данных"}'
                )
    
        image = Image(
            text=unquote(text),  # декодируем очищенный текст (надпись на картинке)
            mime_type=content_type,
            data=response.content
        )
        logger.info(f'Получено изображение {image}')
        
        return image


class YaDiskClient:
    '''Модуль взаимодействия с Яндекс.Диском'''
    def __init__(self, api_url: str, token: str, folder: str):
        self.headers = {'Authorization': f'OAuth {token}'}
        self.base = api_url
        self.folder = folder

    def _is_folder_exists(self) -> bool | None:
        params = {'path': f'/{self.folder}'}
        request_url = f'{self.base}'

        logger.info(f'Проверка наличия каталога "{self.folder}" на Я.Диске')

        try:
            response = requests.get(
                request_url,
                params=params,
                headers=self.headers,
                timeout=settings.http_timeout,
            )
        except (requests.RequestException, ValueError) as error:
            logger.error(f'Не удалось осуществить проверку: {error}')
        else:
            match response.status_code:
                case 200:
                    resource_type = response.json()['type']
                    if resource_type == 'dir':
                        logger.info(f'Каталог "{self.folder}" найден на Я.Диске')
                        return True
                    else:
                        logger.error(
                            f'Найден объект типа {resource_type} с именем "{self.folder}". '
                            f'Я.Диск не позволяет создавать объекты с одинаковымим именами: '
                            f'невозможно создать каталог "{self.folder}" на Я.Диске'
                        )
                case 401:
                    logger.error('Ошибка авторизации на Я.Диске или не задан токен')
                case 404:
                    logger.warning(f'Каталог "{self.folder}" отсутствует на Я.Диске')
                    return False
                case _:
                    logger.error(f'Я.Диск вернул код ответа {response.status_code}')

        return None

    def _create_folder(self) -> bool:
        params = {'path': f'/{self.folder}'}
        request_url = f'{self.base}'

        logger.info(f'Отправлен запрос на создание каталога "{self.folder}" на Я.Диске')

        response = requests.put(
            request_url,
            params=params,
            headers=self.headers,
            timeout=settings.http_timeout
        )
        
        match response.status_code:
            case 201: 
                logger.info(f'Каталог "{self.folder}" создан на Я.Диске') 
                return True
            case 401:
                logger.error('Ошибка авторизации на Я.Диске или не задан токен')
            case 409:
                logger.error('Одноимённый каталог уже существует на Яндекс.Диске')
            case _:
                logger.error(
                    f'Не удалось создать каталог "{self.folder}" на Я.Диске: '
                    f'HTTP-код ответа {response.status_code}'
                )

        return False

    def _ensure_folder_exists(self) -> bool:
        folder_exists_status = self._is_folder_exists()

        if folder_exists_status == None:
            logger.error(
                f'Невозможно подключить или создать каталог "{self.folder}" на Я.Диске'
            )
            return False
        
        if folder_exists_status:
            return True
        else:
            return self._create_folder()
        
    def get_download_url(self, file_name: str) -> tuple[str | None, bool]:
        params = {'path': f'/{self.folder}/{file_name}'}
        request_url = f'{self.base}/download/'

        logger.info(f'Запрос у Я.Диска ссылки для скачивания файла "{file_name}"')       
        
        try:
            response = requests.get(
                request_url,
                params=params,
                headers=self.headers,
                timeout=settings.http_timeout,
            )
        except (requests.RequestException, ValueError) as error:
            logger.error(f'Не удалось получить от Я.Диска ссылку для скачивания файла: {error}')
        else:
            match response.status_code:
                case 200:
                    url = response.json()['href']
                    logger.info(f'От Я.Диска получена ссылка для скачивания файла "{file_name}"')
                    logger.debug(f'Ссылка на скачивание "{file_name}": {url}')
                    return url, True
                case 401:
                    logger.error('Ошибка авторизации на Я.Диске или не задан токен')
                case 404:
                    logger.warning('Указанный файл отсутствует на Я.Диске')
                    return None, True
                case _:
                    logger.error(f'Я.Диск вернул код ответа {response.status_code}')
        
        return None, False

    def get_upload_url(self, file_name: str) -> str | None:
        params = {
            'path': f'/{self.folder}/{file_name}',
            'overwrite': 'true',
        }
        request_url = f'{self.base}/upload/'

        logger.info(f'Запрос ссылки для загрузки файла "{file_name}" на Я.Диск')

        try:
            response = requests.get(
                request_url,
                params=params,
                headers=self.headers,
                timeout=settings.http_timeout,
            )
        except (requests.RequestException, ValueError) as error:
            logger.error(f'Не удалось получить ссылку для загрузки файла на Я.Диск: {error}')
            return None
        else:
            match response.status_code:
                case 200:
                    url = response.json()['href']
                    logger.info(f'От Я.Диска получена ссылка на загрузку файла "{file_name}"')
                    logger.debug(f'Cсылка на загрузку "{file_name}": {url}')
                    return url
                case 401:
                    logger.error('Ошибка авторизации на Я.Диске или не задан токен')
                case 404:
                    logger.error(f'Не удалось найти файл {params["path"]} на Я.Диске')
                case _:
                    logger.error(f'Я.Диск вернул код ответа {response.status_code}')
            return None

    def _download_bytes(self, url: str) -> bytes | None:        
        response = requests.get(url, timeout=settings.http_timeout)
        
        if response.status_code == 200:
            logger.debug('Я.Диск вернул код ответа 200 => "OK"')
            return response.content
        else:
            logger.error(
                f'Не удалось скачать файл с Я.Диска: '
                f'HTTP-код ответа {response.status_code}')
            return None

    def download_file(self, file_name: str) -> bytes | None:
        url, no_errors = self.get_download_url(file_name)
        
        if url:
            logger.info('Скачивание файла с Я.Диска...') 
            return self._download_bytes(url)
        elif no_errors:
            return b''  # возврат пустых байтов, как пустого отчёта (нет файла отчёта)
        else:
            return None

    def _upload_bytes(self, url: str, content: bytes) -> bool:
        response = requests.put(
            url,
            data=content,
            timeout=settings.http_timeout
        )
        
        if response.status_code == 201:
            logger.info('Файл загружен на Я.Диск') 
            logger.debug('Я.Диск вернул код ответа 201 => "OK"')
            return True
        else:
            logger.error(
                f'Не удалось загрузить файл на Я.Диск: '
                f'HTTP-код ответа {response.status_code}'
            )
            return False

    def upload_file(self, file_name: str, content: bytes) -> bool | None:
        url = self.get_upload_url(file_name)

        if url:
            logger.info('Загрузка файла на Я.Диск...')
            return self._upload_bytes(url, content)
        
        return None


class ReportService:
    '''Модуль работы с отчётом о выгруженных на Я.Диск изображениях'''
    def __init__(
        self,
        ya_disk_client: YaDiskClient,
        file_name: str = 'upload_report.json'
    ):
        self.file_name = file_name
        self.ya_disk_client = ya_disk_client
        self.report = []

    def _serialize_report(self, report: list) -> bytes:
        return dumps(report, ensure_ascii=False).encode('utf-8')

    def _deserialize_report(self, content: bytes) -> list:
        if content:
            return loads(content)
        else:
            return []

    def _download_report(self) -> bool:
        raw_data = self.ya_disk_client.download_file(self.file_name)
        
        if raw_data != None:
            self.report = self._deserialize_report(raw_data)
            return True
        else:
            return False

    def _upload_report(self) -> bool:
        raw_data = self._serialize_report(self.report)
        
        return self.ya_disk_client.upload_file(
            self.file_name,
            raw_data
        )

    def update_report(self, image_info: dict) -> bool:
        if self._download_report():
            logger.info(
                f'Дополнение отчёта информацией о файле '
                f'"{image_info["file_name"]}"')
            self.report.append(image_info)
        else:
            logger.error('Не удалось получить файл отчёта о загрузках с Я.Диска')
            return False
        
        if self._upload_report():
            logger.info('Отчёт на Я.Диске о загруженных файлах обновлён')
        else:
            logger.error('Не удалось обновить отчёт о загрузках на Я.Диске')
            return False            

        return True


class AppService:
    '''Общая логика приложения'''
    def __init__(self, cataas_client: CataasClient, ya_disk_client: YaDiskClient):
        self.cataas_client = cataas_client
        self.ya_disk_client = ya_disk_client

    def run(self) -> bool:
        text = input(
            "\nВведите текст для картинки с котиком или q для выхода: "
        ).strip()

        if text.lower() == "q":
            logger.info("Выход из программы по команде пользователя.")
            return False

        if not text:
            logger.warning('Текст не задан, будет запрошено изображение без надписи')

        image = self.cataas_client.get_cat_image(text)

        if image is None:
            return True

        is_uploaded = self.ya_disk_client.upload_file(
            f'{image.name}.{image.extension}',
            image.data
        )
        
        if is_uploaded:
            logger.info(
                f'Изображение "{image.name}.{image.extension}" сохранено на Я.Диск '
                f'в каталог {settings.netology_group_id}'
            )
            
            logger.info(
                f'Формирование отчёта о выгрузке изображения '
                f'"{image.name}.{image.extension}"')
            
            report.update_report(image.info)

        return True

    def run_loop(self) -> None:
        while True:
            if not self.run():
                break


if __name__ == "__main__":
    settings = Settings.load_env_settings()

    logging.basicConfig(
        level=settings.app_logging_level,
        format='[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%H:%M:%S'
    )
    logger = logging.getLogger(__name__)

    logger.debug(f'Настройки приложения:\n{settings}')

    cataas_client = CataasClient(settings.cataas_api_url)

    ya_disk_target_folder = input(
        f'Если требуется, измените название каталога на Я.Диске для загрузки изображений.\n'
        f'Название по умолчанию - "{settings.netology_group_id}": '
    )

    if not ya_disk_target_folder:
        ya_disk_target_folder = settings.netology_group_id

    ya_disk_target_folder = sanitize_filename(ya_disk_target_folder)

    logger.info(f'Задан каталог хранения "{ya_disk_target_folder}" на Я.Диске')

    ya_disk_client = YaDiskClient(
        settings.ya_disk_api_url,
        settings.ya_disk_token,
        ya_disk_target_folder
    )

    if not ya_disk_client._ensure_folder_exists():
        logger.error('Завершение программы.')
        raise SystemExit(1)

    report = ReportService(ya_disk_client)

    AppService(cataas_client, ya_disk_client).run_loop()