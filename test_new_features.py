import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import os
from unittest.mock import Mock, patch

import downloads
import first_run_setup
import llama_runtime
import meeting_titles


class Response(io.BytesIO):
    def __init__(self,data,status=200,headers=None):
        super().__init__(data)
        self.status=status
        self.headers={'Content-Length':str(len(data)),**(headers or {})}


class DownloadTests(unittest.TestCase):
    def test_resume_retains_prefix_and_verifies_completed_file(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'model.gguf'
            target.with_name('model.gguf.partial').write_bytes(b'first')
            with patch.object(downloads.urllib.request,'urlopen',return_value=Response(b'second',206,{'Content-Range':'bytes 5-10/11'})) as network:
                downloads.download_file('https://example.test/model',target,sha256=hashlib.sha256(b'firstsecond').hexdigest())
            self.assertEqual(network.call_args.args[0].get_header('Range'),'bytes=5-')
            self.assertEqual(target.read_bytes(),b'firstsecond')

    def test_server_ignoring_range_replaces_partial_instead_of_appending(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'model'
            Path(str(target)+'.partial').write_bytes(b'old')
            with patch.object(downloads.urllib.request,'urlopen',return_value=Response(b'new')):
                downloads.download_file('https://example.test/model',target)
            self.assertEqual(target.read_bytes(),b'new')

    def test_pause_never_publishes_incomplete_model_and_retry_resumes(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'model';paused=[False]
            with patch.object(downloads.urllib.request,'urlopen',return_value=Response(b'x'*2**21)):
                with self.assertRaises(downloads.DownloadPaused):
                    downloads.download_file('https://example.test/model',target,cancelled=lambda:paused[0],progress=lambda *_:paused.__setitem__(0,True))
            self.assertFalse(target.exists())
            self.assertEqual(Path(str(target)+'.partial').stat().st_size,2**20)

    def test_checksum_failure_cannot_mark_ready(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'model'
            with patch.object(downloads.urllib.request,'urlopen',return_value=Response(b'bad')):
                with self.assertRaisesRegex(RuntimeError,'checksum'):
                    downloads.download_file('https://example.test/model',target,sha256='0'*64)
            self.assertFalse(target.exists())


class MeetingTests(unittest.TestCase):
    def test_windows_and_browser_title_detection(self):
        for owner,title,app,name in [('ms-teams.exe','Design review | Microsoft Teams','Teams','Design review'),('zoom.us','Weekly planning','Zoom','Weekly planning'),('Google Chrome','Meet - Product sync - Google Chrome','Google Meet','Product sync'),('Feishu','产品讨论','Feishu','产品讨论'),('腾讯会议','项目周会','VooV','项目周会')]:
            result=meeting_titles.classify(owner,title)
            self.assertIsNotNone(result,(owner,title))
            self.assertEqual(result[0],app)
            self.assertEqual(result[1],name)
        self.assertIsNone(meeting_titles.classify('Google Chrome','Shopping'))

    def test_active_meeting_ranking_duplicates_and_generic_titles(self):
        with patch.object(meeting_titles,'visible_windows',return_value=iter([('zoom.us','Zoom Workplace',1,True),('Teams','项目会议',2,False),('Teams','项目会议',2,False)])):
            meetings=meeting_titles.detect_meetings()
        self.assertEqual(len(meetings),2)
        self.assertEqual(meetings[0].title,'项目会议')
        self.assertEqual(meetings[1].title,'')

    def test_filename_is_bounded_and_cannot_create_subdirectories(self):
        stem=meeting_titles.meeting_stem('20261116_1430','项目/评审:*?'+ '长'*100,'Teams')
        self.assertNotIn('/',stem)
        self.assertNotIn(':',stem)
        self.assertLess(len(stem.encode()),180)
        self.assertEqual(meeting_titles.safe_name('CON'),'_CON')
        self.assertIn('Unnamed meeting',meeting_titles.meeting_stem('20261116_1430'))


class SetupTests(unittest.TestCase):
    def test_new_model_download_pins_revision_and_hash_before_ready(self):
        from types import SimpleNamespace as S
        with tempfile.TemporaryDirectory() as d:
            setup=first_run_setup.LocalModelSetup();complete=Mock()
            info=S(sha='pinned-commit',siblings=[S(rfilename='model-Q4_K_M.gguf',lfs=S(sha256='a'*64))])
            with patch.object(first_run_setup,'ensure_runtime',return_value='server'),patch('huggingface_hub.HfApi') as api,patch.object(first_run_setup,'download_file',return_value=Path(d)/'model-Q4_K_M.gguf') as download:
                api.return_value.model_info.return_value=info
                setup.run(d,{},dict(repo_id='example/model',quant='Q4_K_M'),d,complete)
            self.assertEqual(setup.state,'ready')
            self.assertIn('pinned-commit',download.call_args.args[0])
            self.assertEqual(download.call_args.kwargs['sha256'],'a'*64)
            complete.assert_called_once()

    def test_existing_custom_model_is_preserved_without_model_network(self):
        with tempfile.TemporaryDirectory() as d:
            model=Path(d)/'custom.gguf';model.write_bytes(b'GGUF')
            setup=first_run_setup.LocalModelSetup();complete=Mock()
            with patch.object(first_run_setup,'ensure_runtime',return_value='server'),patch.object(first_run_setup,'download_file') as download:
                setup.run(d,{'llm_server_model_path':str(model)},None,d,complete)
            self.assertEqual(setup.state,'ready');complete.assert_called_once_with(model);download.assert_not_called()

    def test_setup_failure_is_retryable_and_never_completes(self):
        setup=first_run_setup.LocalModelSetup();complete=Mock()
        with patch.object(first_run_setup,'ensure_runtime',side_effect=RuntimeError('offline')):
            setup.run('/tmp',{},None,'/tmp',complete)
        self.assertEqual(setup.state,'failed');complete.assert_not_called()

    def test_mac_tar_runtime_keeps_bin_lib_layout_and_exec_permissions(self):
        data=io.BytesIO()
        with tarfile.open(fileobj=data,mode='w:gz') as tar:
            for name,payload in [('build/bin/llama-server',b'server'),('build/lib/libllama.dylib',b'library')]:
                member=tarfile.TarInfo(name);member.size=len(payload);member.mode=0o644
                tar.addfile(member,io.BytesIO(payload))
        raw=data.getvalue()
        asset={'name':'llama-b123-bin-macos-arm64.tar.gz','browser_download_url':'https://github.com/ggml-org/llama.cpp/releases/download/b123/runtime.tar.gz','digest':'sha256:'+hashlib.sha256(raw).hexdigest()}
        with tempfile.TemporaryDirectory() as d,patch.object(llama_runtime,'find_runtime',return_value=None),patch.object(llama_runtime.platform,'system',return_value='Darwin'),patch.object(llama_runtime.platform,'machine',return_value='arm64'),patch.object(llama_runtime,'_open',side_effect=[Response(json.dumps([{'assets':[asset]}]).encode()),Response(raw)]):
            server=Path(llama_runtime.ensure_runtime(Path(d)))
            if os.name != "nt":
                self.assertTrue(server.stat().st_mode & 0o111)
            self.assertEqual((server.parent.parent/'lib/libllama.dylib').read_bytes(),b'library')


class SettingsIntegrationTests(unittest.TestCase):
    def test_summary_presets_and_new_slice_defaults(self):
        import main
        self.assertIsNotNone(main.llm_preset_by_id('qwen3.5-0.8b-q4-0'))
        self.assertEqual(sum(p['id']=='qwen3-0.6b-q8' for p in main.LLM_MODEL_PRESETS),1)
        for language in ['en','zh']:
            cfg=main.default_realtime_settings()['languages'][language]
            self.assertEqual((cfg['transcribe_seconds'],cfg['overlap_seconds']),(30,2))
        offline=main.default_offline_transcript_settings()
        self.assertEqual((offline['slice_seconds'],offline['overlap_seconds']),(30,2))

    def test_first_launch_only_offers_settings_once_without_downloading(self):
        import main
        for choice in [False,True]:
            app=main.MeetingRecorderApp.__new__(main.MeetingRecorderApp)
            app.shutting_down=False;app.model_settings_prompt_seen=False
            app.root=Mock();app.title=Mock(return_value='ATR')
            app.save_user_settings=Mock();app.show_main_tab=Mock()
            app.system_settings_window=Mock();app.initial_llm_setup=Mock()
            app.realtime_settings=main.default_realtime_settings()
            app.realtime_settings['llm_auto_setup']=True  # Old saved option must not restore auto-download.
            with patch.dict(os.environ,{'ATR_DISABLE_AUTO_SETUP':'0'}),patch.object(main.messagebox,'askyesno',return_value=choice) as ask:
                app.offer_model_settings();app.offer_model_settings()
                app.start_initial_llm_setup()
            ask.assert_called_once();app.save_user_settings.assert_called_once()
            app.initial_llm_setup.start.assert_not_called()
            if choice:
                app.show_main_tab.assert_called_once_with('settings')
                app.system_settings_window.model_settings_notebook.select.assert_called_once_with(1)
            else:app.show_main_tab.assert_not_called()

    def test_saved_custom_slices_and_prompt_marker_survive_loading(self):
        import main
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'settings.json'
            path.write_text(json.dumps({'model_settings_prompt_seen':True,'realtime_settings':{'llm_server_managed':True,'languages':{'en':{'transcribe_seconds':15,'overlap_seconds':0}}},'offline_transcript_settings':{'slice_seconds':12,'overlap_seconds':1}}))
            app=main.MeetingRecorderApp.__new__(main.MeetingRecorderApp)
            with patch.object(main,'user_settings_path',return_value=path):app.load_user_settings()
            self.assertTrue(app.model_settings_prompt_seen)
            cfg=app.realtime_settings['languages']['en']
            self.assertEqual((cfg['transcribe_seconds'],cfg['overlap_seconds']),(15,0))
            self.assertEqual(app.offline_transcript_settings['slice_seconds'],12)

    def test_model_selection_offline_backend_and_editable_meeting_title(self):
        import tkinter as tk
        import main
        import asr_models
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'ATR_DATA_DIR':d,'ATR_DISABLE_AUTO_SETUP':'1'}):
            root=tk.Tk();root.withdraw()
            try:
                app=main.MeetingRecorderApp(root)
                app.show_main_tab()
                panel=app.system_settings_window
                for language in ['en','zh']:
                    for preset in asr_models.presets(language):
                        panel.apply_model_preset(language,preset,auto_download=False)
                        app.set_realtime_settings(panel.collect_values())
                        self.assertEqual(app.realtime_language_settings(language)['engine'],preset['engine'])
                        self.assertEqual(app.realtime_model_path(language),str(main.whisper_preset_local_dir(preset)))
                app.load_user_settings()
                app.offline_window.language_var.set('zh')
                self.assertEqual(app.offline_window.current_config()['engine'],'paraformer')
                with patch.object(main,'detect_meetings',return_value=[meeting_titles.Meeting('Detected title','Zoom')]):
                    app.show_recording_options_window()
                    self.assertEqual(app.meeting_name_var.get(),'Detected title')
                    app.meeting_name_var.set('人工标题')
                    app.retry_meeting_detection(app.recording_options_window)
                    self.assertEqual(app.meeting_name_var.get(),'人工标题')
                    with patch.object(app,'begin_recording') as begin:
                        app.accept_recording_options()
                    self.assertEqual(begin.call_args.args[-2:],('人工标题','Zoom'))
            finally:
                for callback in root.tk.call("after", "info"):
                    root.tk.call("after", "cancel", callback)
                root.destroy()


if __name__=='__main__':unittest.main()
