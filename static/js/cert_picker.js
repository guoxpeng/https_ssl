/* 证书选择器：验证页与反向代理页共用。
 *
 * 两个页面使用同一套元素 id，因此一套逻辑即可复用：
 *   input[name="cert-type"]        证书类型单选（qilin / custom）
 *   #qilin-cert-select             证书下拉容器
 *   #cert-list                     证书下拉
 *   #cert-files                    自定义证书文件区域
 *   #cert-file / #key-file         证书 / 私钥文件输入
 *   #cert-filename / #key-filename 文件名显示
 *   .upload-btn（data-target=...）  触发对应文件选择的按钮
 *
 * 自定义证书在「选择文件」时立即上传，提交表单时只传服务端返回的存储名，
 * 因此中文名、空格、重名文件都不会影响后端落盘。
 */
(function ($) {
    'use strict';

    var state = { cert: null, key: null };

    function errorMessage(xhr, fallback) {
        var body = xhr && xhr.responseJSON;
        return (body && body.message) ? body.message : fallback;
    }

    function setLabel($label, text) {
        if ($label.length) {
            $label.text(text).attr('title', text);
        }
    }

    function uploadFile(input, kind, $label) {
        var file = input.files && input.files[0];
        if (!file) {
            return;
        }
        var formData = new FormData();
        formData.append('file', file);
        $.ajax({
            url: '/upload', type: 'POST', data: formData,
            processData: false, contentType: false
        }).done(function (response) {
            if (!response.success) {
                alert('文件上传失败：' + response.message);
                input.value = '';
                return;
            }
            state[kind] = {
                stored: response.filename,
                original: response.original || file.name
            };
            setLabel($label, state[kind].original);
        }).fail(function (xhr) {
            alert('文件上传失败：' + errorMessage(xhr, '请稍后重试'));
            input.value = '';
        });
    }

    function loadCertOptions(selectedId) {
        var $select = $('#cert-list');
        $select.empty().append($('<option>').val('').text('请选择证书'));
        $.ajax({
            url: '/list_certs', type: 'GET',
            headers: { Accept: 'application/json' }
        }).done(function (response) {
            var certs = (response && response.certs) || [];
            if (!certs.length) {
                $select.append($('<option>').val('').text('暂无可用证书').prop('disabled', true));
                return;
            }
            $.each(certs, function (_i, cert) {
                $select.append($('<option>').val(cert.name).text(cert.name));
            });
            // 只有选项真实存在才选中：直接 val() 一个不存在的值会把
            // selectedIndex 置为 -1，下拉看起来是空的，反而更难用。
            if (selectedId) {
                var hit = $select.find('option').filter(function () {
                    return this.value === selectedId;
                }).length;
                if (hit) {
                    $select.val(selectedId);
                }
            }
        }).fail(function (xhr) {
            alert('获取证书列表失败：' + errorMessage(xhr, '请稍后重试'));
        });
    }

    /* 切换证书类型：qilin 显示证书下拉，custom 显示文件上传。 */
    function applyType(type, selectedId) {
        if (type === 'qilin') {
            $('#qilin-cert-select').show();
            $('#cert-files').hide();
            loadCertOptions(selectedId);
        } else {
            $('#qilin-cert-select').hide();
            $('#cert-files').show();
        }
    }

    window.QilinCertPicker = {
        /* 绑定证书类型切换、文件选择与上传，只需调用一次。 */
        init: function () {
            $('input[name="cert-type"]').on('change', function () {
                applyType($(this).val());
            });
            $('.upload-btn').on('click', function () {
                $('#' + $(this).data('target')).trigger('click');
            });
            $('#cert-file').on('change', function () {
                uploadFile(this, 'cert', $('#cert-filename'));
            });
            $('#key-file').on('change', function () {
                uploadFile(this, 'key', $('#key-filename'));
            });
            applyType($('input[name="cert-type"]:checked').val() || 'qilin');
        },

        show: function (type) {
            applyType(type);
        },

        /* 编辑场景：按已有配置回填证书选择。 */
        preload: function (options) {
            options = options || {};
            var type = options.certType || 'qilin';
            $('input[name="cert-type"][value="' + type + '"]').prop('checked', true);
            if (type === 'qilin') {
                // 旧版本记录只有 cert_type，没有 cert_id / cert_filename，
                // 这里按「cert_id → 证书文件名 → 服务名」依次兜底，与后端
                // create_proxy 的 `cert_id or service_name` 保持一致，
                // 编辑老服务时下拉框能自动选中原证书。
                var certId = options.certId || '';
                if (!certId && options.certStored) {
                    certId = String(options.certStored).replace(/\.(crt|pem|cer)$/i, '');
                }
                if (!certId) {
                    certId = options.serviceName || '';
                }
                applyType('qilin', certId);
                return;
            }
            if (options.certStored) {
                state.cert = { stored: options.certStored, original: options.certDisplay || options.certStored };
            }
            if (options.keyStored) {
                state.key = { stored: options.keyStored, original: options.keyDisplay || options.keyStored };
            }
            setLabel($('#cert-filename'), state.cert ? state.cert.original : '未选择文件');
            setLabel($('#key-filename'), state.key ? state.key.original : '未选择文件');
            applyType('custom');
        },

        /* 清空选择，用于「新增」时重置弹窗。 */
        reset: function () {
            state = { cert: null, key: null };
            $('#cert-file, #key-file').val('');
            $('#cert-list').val('');
            setLabel($('#cert-filename'), '未选择文件');
            setLabel($('#key-filename'), '未选择文件');
        },

        uploaded: function () {
            return state;
        },

        selectedCertId: function () {
            return $('#cert-list').val() || '';
        },

        /* 提交自定义证书前确认两个文件都已上传。 */
        validateCustom: function () {
            if (!state.cert || !state.key) {
                alert('请上传证书和私钥文件');
                return false;
            }
            return true;
        }
    };
})(jQuery);
