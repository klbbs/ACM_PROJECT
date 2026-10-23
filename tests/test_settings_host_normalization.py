from acmweb.settings import Settings


def test_allowed_host_configuration_normalized_before_origin_validation(tmp_path):
    settings = Settings(data_dir=tmp_path, production=True,
                        access_token='synthetic-test-token-long-enough',
                        allowed_hosts=(' ACM.EXAMPLE.TEST. ', 'acm.example.test', ' '),
                        public_origin='https://acm.example.test')
    settings.validate()
    assert settings.allowed_hosts == ('acm.example.test',)
