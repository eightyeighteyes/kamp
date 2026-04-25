---
id: TASK-181
title: crash during bandcamp sync
status: To Do
assignee: []
created_date: '2026-04-25 14:36'
labels: []
milestone: m-31
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
occurred in built application with one-time sync running:

-------------------------------------
Translated Report (Full Report Below)
-------------------------------------
Process:             Kamp [18522]
Path:                /Applications/Kamp.app/Contents/MacOS/Kamp
Identifier:          com.kamp.app
Version:             1.12.0 (1.12.0)
Code Type:           X86-64 (Native)
Role:                Foreground
Parent Process:      launchd [1]
Coalition:           com.kamp.app [13381]
User ID:             501

Date/Time:           2026-04-25 10:31:11.4108 -0400
Launch Time:         2026-04-25 10:27:06.8880 -0400
Hardware Model:      MacBookPro16,1
OS Version:          macOS 26.4.1 (25E253)
Release Type:        User
Bridge OS Version:     10.4 (23P4242)

Crash Reporter Key:  456A5EAC-AA57-92DE-2142-B1399DA39396
Incident Identifier: 77F91CF7-A5BF-4C64-B880-BAF310F8C478

Sleep/Wake UUID:       2643419C-D035-43EE-886F-755BAAA1F190

Time Awake Since Boot: 36000 seconds
Time Since Wake:       12040 seconds

System Integrity Protection: enabled

Triggered by Thread: 0  CrBrowserMain, Dispatch Queue: com.apple.main-thread

Exception Type:    EXC_BREAKPOINT (SIGTRAP)
Exception Codes:   0x0000000000000002, 0x0000000000000000

Termination Reason:  Namespace SIGNAL, Code 5, Trace/BPT trap: 5
Terminating Process: exc handler [18522]

Thread 0 Crashed:: CrBrowserMain Dispatch queue: com.apple.main-thread
0   Electron Framework            	       0x115b4c8af ares_dns_rr_get_ttl + 4177919
1   Electron Framework            	       0x115b4c8c9 ares_dns_rr_get_ttl + 4177945
2   Electron Framework            	       0x115b4c8e6 ares_dns_rr_get_ttl + 4177974
3   Electron Framework            	       0x112962047 node::PrincipalRealm::tick_callback_function() const + 476951
4   Electron Framework            	       0x113bb9de7 v8::Isolate::Free(v8::Isolate*) + 7143031
5   Electron Framework            	       0x113bb9d86 v8::Isolate::Free(v8::Isolate*) + 7142934
6   Electron Framework            	       0x10fe051b8 cppgc::internal::PersistentRegionLock::AssertLocked() + 20392
7   Electron Framework            	       0x110a0a4c4 rust_png$cxxbridge1$194$Reader$width + 125860
8   Electron Framework            	       0x11147a052 node::BaseObject::TransferForMessaging() + 36738
9   Electron Framework            	       0x110be0aea v8::internal::OptimizingCompileTaskExecutor::WaitUntilCompilationJobsDoneForIsolate(v8::internal::Isolate*) + 32330
10  Electron Framework            	       0x110242c1a v8::internal::ThreadIsolation::LookupJitAllocation(unsigned long, unsigned long, v8::internal::ThreadIsolation::JitAllocationType, bool) + 57642
11  Electron Framework            	       0x110242705 v8::internal::ThreadIsolation::LookupJitAllocation(unsigned long, unsigned long, v8::internal::ThreadIsolation::JitAllocationType, bool) + 56341
12  Electron Framework            	       0x1145144b6 _v8_internal_Node_Print(void*) + 3922166
13  ???                           	       0x1744d8153 ???
14  Electron Framework            	       0x1144a7aad _v8_internal_Node_Print(void*) + 3477229
15  Electron Framework            	       0x11459562e _v8_internal_Node_Print(void*) + 4450926
16  Electron Framework            	       0x11449531e _v8_internal_Node_Print(void*) + 3401566
17  Electron Framework            	       0x1144601eb _v8_internal_Node_Print(void*) + 3184171
18  Electron Framework            	       0x110dad0a6 v8::internal::ThreadIsolation::RegisterJitPage(unsigned long, unsigned long) + 185558
19  Electron Framework            	       0x10fd0b4ca v8::Isolate::SuppressMicrotaskExecutionScope::SuppressMicrotaskExecutionScope(v8::Isolate*, v8::MicrotaskQueue*) + 250
20  Electron Framework            	       0x10fd0b46b v8::Isolate::SuppressMicrotaskExecutionScope::SuppressMicrotaskExecutionScope(v8::Isolate*, v8::MicrotaskQueue*) + 155
21  Electron Framework            	       0x10fd0b0ed cppgc::internal::TraceTraitFromInnerAddressImpl::GetTraceDescriptor(void const*) + 1021
22  Electron Framework            	       0x112a1b928 node::InternalCallbackScope::Close() + 328
23  Electron Framework            	       0x112a1b391 node::CallbackScope::~CallbackScope() + 49
24  Electron Framework            	       0x1128cdf96 node::crypto::TLSWrap::GetFD() + 1058438
25  Electron Framework            	       0x1125b9695 v8::internal::compiler::CompilationDependencies::DependOnContextCell(v8::internal::compiler::ContextRef, unsigned long, v8::internal::ContextCell::State, v8::internal::compiler::JSHeapBroker*) + 591973
26  Electron Framework            	       0x10fce8a35 v8::Promise::Resolver::GetPromise() + 10389
27  Electron Framework            	       0x11066fe9c v8::FunctionTemplate::New(v8::Isolate*, void (*)(v8::FunctionCallbackInfo<v8::Value> const&), v8::Local<v8::Value>, v8::Local<v8::Signature>, int, v8::ConstructorBehavior, v8::SideEffectType, v8::CFunction const*, unsigned short, unsigned short, unsigned short) + 37596
28  Electron Framework            	       0x10fe4f67a v8::internal::compiler::CompilationDependencies::FieldRepresentationDependencyOffTheRecord(v8::internal::compiler::MapRef, v8::internal::compiler::MapRef, v8::internal::InternalIndex, v8::internal::Representation) const + 211658
29  Electron Framework            	       0x1125c2adf v8::internal::compiler::CompilationDependencies::DependOnContextCell(v8::internal::compiler::ContextRef, unsigned long, v8::internal::ContextCell::State, v8::internal::compiler::JSHeapBroker*) + 629935
30  CoreFoundation                	    0x7ff8163dc188 __CFRUNLOOP_IS_CALLING_OUT_TO_A_SOURCE0_PERFORM_FUNCTION__ + 17
31  CoreFoundation                	    0x7ff8163dc12a __CFRunLoopDoSource0 + 157
32  CoreFoundation                	    0x7ff8163dbeea __CFRunLoopDoSources0 + 203
33  CoreFoundation                	    0x7ff8163daba2 __CFRunLoopRun + 916
34  CoreFoundation                	    0x7ff8164a28b7 _CFRunLoopRunSpecificWithOptions + 496
35  HIToolbox                     	    0x7ff823111b8b RunCurrentEventLoopInMode + 283
36  HIToolbox                     	    0x7ff823114c4d ReceiveNextEventCommon + 599
37  HIToolbox                     	    0x7ff82329d0ba _BlockUntilNextEventMatchingListInMode + 37
38  AppKit                        	    0x7ff81a956d34 _DPSBlockUntilNextEventMatchingListInMode + 172
39  AppKit                        	    0x7ff81a24c9d5 _DPSNextEvent + 800
40  AppKit                        	    0x7ff81aefab53 -[NSApplication(NSEventRouting) _nextEventMatchingEventMask:untilDate:inMode:dequeue:] + 1265
41  AppKit                        	    0x7ff81aefa629 -[NSApplication(NSEventRouting) nextEventMatchingMask:untilDate:inMode:dequeue:] + 67
42  AppKit                        	    0x7ff81a23d868 -[NSApplication run] + 472
43  Electron Framework            	       0x111d9a108 v8::internal::ThreadIsolation::UnregisterWasmAllocation(unsigned long, unsigned long) + 12040
44  Electron Framework            	       0x11188c222 node::PrincipalRealm::get_source_map_error_source() const + 76018
45  Electron Framework            	       0x110c517f9 v8::Isolate::Allocate() + 74777
46  Electron Framework            	       0x110f62fc1 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1777
47  Electron Framework            	       0x111a31257 node::PrincipalRealm::http2session_on_headers_function() const + 31863
48  Electron Framework            	       0x111a311a2 node::PrincipalRealm::http2session_on_headers_function() const + 31682
49  Electron Framework            	       0x111ae43cc v8::Isolate::RequestInterrupt(void (*)(v8::Isolate*, void*), void*) + 281244
50  Electron Framework            	       0x112ef972a v8::CpuProfile::GetSamplesCount() const + 1149242
51  Electron Framework            	       0x112efa78f v8::CpuProfile::GetSamplesCount() const + 1153439
52  Electron Framework            	       0x112efa56d v8::CpuProfile::GetSamplesCount() const + 1152893
53  Electron Framework            	       0x1118a0411 node::PrincipalRealm::get_source_map_error_source() const + 158433
54  Electron Framework            	       0x11189fd60 node::PrincipalRealm::get_source_map_error_source() const + 156720
55  Electron Framework            	       0x1127a67a4 ElectronMain + 132
56  dyld                          	    0x7ff815f4abb8 start + 3240

Thread 1:: com.apple.NSEventThread
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   CoreFoundation                	    0x7ff8163dc2bf __CFRunLoopServiceMachPort + 145
5   CoreFoundation                	    0x7ff8163dad58 __CFRunLoopRun + 1354
6   CoreFoundation                	    0x7ff8164a28b7 _CFRunLoopRunSpecificWithOptions + 496
7   AppKit                        	    0x7ff81a39e6a0 _NSEventThread + 158
8   libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
9   libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 2:: PerfettoTrace
0   libsystem_kernel.dylib        	    0x7ff8162e64ca kevent64 + 10
1   Electron Framework            	       0x10ff09534 node::PrincipalRealm::snapshot_serialize_callback() const + 56548
2   Electron Framework            	       0x110c517f9 v8::Isolate::Allocate() + 74777
3   Electron Framework            	       0x110f62fc1 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1777
4   Electron Framework            	       0x110f62ee8 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1560
5   Electron Framework            	       0x115b22d2d ares_dns_rr_get_ttl + 4007037
6   Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
7   libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
8   libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 3:: ThreadPoolServiceThread
0   libsystem_kernel.dylib        	    0x7ff8162e64ca kevent64 + 10
1   Electron Framework            	       0x10ff09534 node::PrincipalRealm::snapshot_serialize_callback() const + 56548
2   Electron Framework            	       0x110c517f9 v8::Isolate::Allocate() + 74777
3   Electron Framework            	       0x110f62fc1 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1777
4   Electron Framework            	       0x110f62ee8 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1560
5   Electron Framework            	       0x110f62e8d v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1469
6   Electron Framework            	       0x115b22d2d ares_dns_rr_get_ttl + 4007037
7   Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
8   libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
9   libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 4:: ThreadPoolForegroundWorker
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   Electron Framework            	       0x1101c9db1 node::PrincipalRealm::inspector_disable_async_hooks() const + 79121
5   Electron Framework            	       0x1101c9b9a node::PrincipalRealm::inspector_disable_async_hooks() const + 78586
6   Electron Framework            	       0x1101c9b44 node::PrincipalRealm::inspector_disable_async_hooks() const + 78500
7   Electron Framework            	       0x10fe0fb43 cppgc::internal::PersistentRegionLock::AssertLocked() + 63795
8   Electron Framework            	       0x10fe0f40d cppgc::internal::PersistentRegionLock::AssertLocked() + 61949
9   Electron Framework            	       0x10fe0f317 cppgc::internal::PersistentRegionLock::AssertLocked() + 61703
10  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
11  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
12  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 5:: ThreadPoolBackgroundWorker
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   Electron Framework            	       0x1101c9db1 node::PrincipalRealm::inspector_disable_async_hooks() const + 79121
5   Electron Framework            	       0x1101c9b9a node::PrincipalRealm::inspector_disable_async_hooks() const + 78586
6   Electron Framework            	       0x1101c9b44 node::PrincipalRealm::inspector_disable_async_hooks() const + 78500
7   Electron Framework            	       0x10fe0fb43 cppgc::internal::PersistentRegionLock::AssertLocked() + 63795
8   Electron Framework            	       0x10fe0f43d cppgc::internal::PersistentRegionLock::AssertLocked() + 61997
9   Electron Framework            	       0x10fe0f340 cppgc::internal::PersistentRegionLock::AssertLocked() + 61744
10  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
11  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
12  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 6:: ThreadPoolForegroundWorker
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   Electron Framework            	       0x1101c9db1 node::PrincipalRealm::inspector_disable_async_hooks() const + 79121
5   Electron Framework            	       0x1101c9b9a node::PrincipalRealm::inspector_disable_async_hooks() const + 78586
6   Electron Framework            	       0x1101c9b44 node::PrincipalRealm::inspector_disable_async_hooks() const + 78500
7   Electron Framework            	       0x10fe0fb43 cppgc::internal::PersistentRegionLock::AssertLocked() + 63795
8   Electron Framework            	       0x10fe0f40d cppgc::internal::PersistentRegionLock::AssertLocked() + 61949
9   Electron Framework            	       0x10fe0f317 cppgc::internal::PersistentRegionLock::AssertLocked() + 61703
10  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
11  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
12  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 7:: Chrome_IOThread
0   libsystem_kernel.dylib        	    0x7ff8162e64ca kevent64 + 10
1   Electron Framework            	       0x10ff09534 node::PrincipalRealm::snapshot_serialize_callback() const + 56548
2   Electron Framework            	       0x110c517f9 v8::Isolate::Allocate() + 74777
3   Electron Framework            	       0x110f62fc1 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1777
4   Electron Framework            	       0x110f62ee8 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1560
5   Electron Framework            	       0x11185ea17 v8::internal::OptimizingCompileTaskExecutor::OptimizingCompileTaskExecutor() + 75447
6   Electron Framework            	       0x115b22d2d ares_dns_rr_get_ttl + 4007037
7   Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
8   libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
9   libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 8:: MemoryInfra
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   Electron Framework            	       0x1101c9db1 node::PrincipalRealm::inspector_disable_async_hooks() const + 79121
5   Electron Framework            	       0x1101c9b9a node::PrincipalRealm::inspector_disable_async_hooks() const + 78586
6   Electron Framework            	       0x110c50346 v8::Isolate::Allocate() + 69478
7   Electron Framework            	       0x110c501c2 v8::Isolate::Allocate() + 69090
8   Electron Framework            	       0x110c517f9 v8::Isolate::Allocate() + 74777
9   Electron Framework            	       0x110f62fc1 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1777
10  Electron Framework            	       0x110f62ee8 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1560
11  Electron Framework            	       0x115b22d2d ares_dns_rr_get_ttl + 4007037
12  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
13  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
14  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 9:: DelayedTaskSchedulerWorker
0   libsystem_kernel.dylib        	    0x7ff8162e17ca kevent + 10
1   Electron Framework            	       0x1127a5ee0 uv__io_poll + 1376
2   Electron Framework            	       0x112793091 uv_run + 481
3   Electron Framework            	       0x112bc05f0 node::WorkerThreadsTaskRunner::DelayedTaskScheduler::Start()::'lambda'(void*)::__invoke(void*) + 160
4   libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
5   libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 10:: V8Worker
0   libsystem_kernel.dylib        	    0x7ff8162df70e __psynch_cvwait + 10
1   libsystem_pthread.dylib       	    0x7ff8163211f7 _pthread_cond_wait + 994
2   Electron Framework            	       0x1127a0c83 uv_cond_wait + 35
3   Electron Framework            	       0x112bc07f2 node::WorkerThreadsTaskRunner::DelayedTaskScheduler::FlushTasks(uv_async_s*) + 466
4   Electron Framework            	       0x112bbdc75 node::WorkerThreadsTaskRunner::WorkerThreadsTaskRunner(int, node::PlatformDebugLogLevel) + 1733
5   libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
6   libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 11:: V8Worker
0   libsystem_kernel.dylib        	    0x7ff8162df70e __psynch_cvwait + 10
1   libsystem_pthread.dylib       	    0x7ff8163211f7 _pthread_cond_wait + 994
2   Electron Framework            	       0x1127a0c83 uv_cond_wait + 35
3   Electron Framework            	       0x112bc07f2 node::WorkerThreadsTaskRunner::DelayedTaskScheduler::FlushTasks(uv_async_s*) + 466
4   Electron Framework            	       0x112bbdc75 node::WorkerThreadsTaskRunner::WorkerThreadsTaskRunner(int, node::PlatformDebugLogLevel) + 1733
5   libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
6   libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 12:: V8Worker
0   libsystem_kernel.dylib        	    0x7ff8162df70e __psynch_cvwait + 10
1   libsystem_pthread.dylib       	    0x7ff8163211f7 _pthread_cond_wait + 994
2   Electron Framework            	       0x1127a0c83 uv_cond_wait + 35
3   Electron Framework            	       0x112bc07f2 node::WorkerThreadsTaskRunner::DelayedTaskScheduler::FlushTasks(uv_async_s*) + 466
4   Electron Framework            	       0x112bbdc75 node::WorkerThreadsTaskRunner::WorkerThreadsTaskRunner(int, node::PlatformDebugLogLevel) + 1733
5   libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
6   libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 13:: SignalInspector
0   libsystem_kernel.dylib        	    0x7ff8162dcaca semaphore_wait_trap + 10
1   Electron Framework            	       0x1127a0b00 uv_sem_wait + 16
2   Electron Framework            	       0x112d64eef node::inspector::Agent::GetWsUrl() const + 79
3   libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
4   libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 14:: NetworkConfigWatcher
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   CoreFoundation                	    0x7ff8163dc2bf __CFRunLoopServiceMachPort + 145
5   CoreFoundation                	    0x7ff8163dad58 __CFRunLoopRun + 1354
6   CoreFoundation                	    0x7ff8164a28b7 _CFRunLoopRunSpecificWithOptions + 496
7   Foundation                    	    0x7ff817645c87 -[NSRunLoop(NSRunLoop) runMode:beforeDate:] + 216
8   Electron Framework            	       0x11188c389 node::PrincipalRealm::get_source_map_error_source() const + 76377
9   Electron Framework            	       0x11188c222 node::PrincipalRealm::get_source_map_error_source() const + 76018
10  Electron Framework            	       0x110c517f9 v8::Isolate::Allocate() + 74777
11  Electron Framework            	       0x110f62fc1 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1777
12  Electron Framework            	       0x110f62ee8 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1560
13  Electron Framework            	       0x115b22d2d ares_dns_rr_get_ttl + 4007037
14  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
15  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
16  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 15:: CrShutdownDetector
0   libsystem_kernel.dylib        	    0x7ff8162dd5d2 read + 10
1   Electron Framework            	       0x1129d65e7 node::PrincipalRealm::tick_callback_function() const + 953527
2   Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
3   libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
4   libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 16:: NetworkConfigWatcher
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   CoreFoundation                	    0x7ff8163dc2bf __CFRunLoopServiceMachPort + 145
5   CoreFoundation                	    0x7ff8163dad58 __CFRunLoopRun + 1354
6   CoreFoundation                	    0x7ff8164a28b7 _CFRunLoopRunSpecificWithOptions + 496
7   Foundation                    	    0x7ff817645c87 -[NSRunLoop(NSRunLoop) runMode:beforeDate:] + 216
8   Electron Framework            	       0x11188c389 node::PrincipalRealm::get_source_map_error_source() const + 76377
9   Electron Framework            	       0x11188c222 node::PrincipalRealm::get_source_map_error_source() const + 76018
10  Electron Framework            	       0x110c517f9 v8::Isolate::Allocate() + 74777
11  Electron Framework            	       0x110f62fc1 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1777
12  Electron Framework            	       0x110f62ee8 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1560
13  Electron Framework            	       0x115b22d2d ares_dns_rr_get_ttl + 4007037
14  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
15  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
16  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 17:: ThreadPoolForegroundWorker
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   Electron Framework            	       0x1101c9db1 node::PrincipalRealm::inspector_disable_async_hooks() const + 79121
5   Electron Framework            	       0x1101c9b9a node::PrincipalRealm::inspector_disable_async_hooks() const + 78586
6   Electron Framework            	       0x1101c9b44 node::PrincipalRealm::inspector_disable_async_hooks() const + 78500
7   Electron Framework            	       0x10fe0fb43 cppgc::internal::PersistentRegionLock::AssertLocked() + 63795
8   Electron Framework            	       0x10fe0f40d cppgc::internal::PersistentRegionLock::AssertLocked() + 61949
9   Electron Framework            	       0x10fe0f317 cppgc::internal::PersistentRegionLock::AssertLocked() + 61703
10  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
11  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
12  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 18:: ThreadPoolForegroundWorker
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   Electron Framework            	       0x1101c9db1 node::PrincipalRealm::inspector_disable_async_hooks() const + 79121
5   Electron Framework            	       0x1101c9b9a node::PrincipalRealm::inspector_disable_async_hooks() const + 78586
6   Electron Framework            	       0x1101c9b44 node::PrincipalRealm::inspector_disable_async_hooks() const + 78500
7   Electron Framework            	       0x10fe0fb43 cppgc::internal::PersistentRegionLock::AssertLocked() + 63795
8   Electron Framework            	       0x10fe0f40d cppgc::internal::PersistentRegionLock::AssertLocked() + 61949
9   Electron Framework            	       0x10fe0f317 cppgc::internal::PersistentRegionLock::AssertLocked() + 61703
10  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
11  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
12  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 19:: ThreadPoolSingleThreadForegroundBlocking0
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   Electron Framework            	       0x1101c9db1 node::PrincipalRealm::inspector_disable_async_hooks() const + 79121
5   Electron Framework            	       0x1101c9b9a node::PrincipalRealm::inspector_disable_async_hooks() const + 78586
6   Electron Framework            	       0x1101c9b44 node::PrincipalRealm::inspector_disable_async_hooks() const + 78500
7   Electron Framework            	       0x10fe0fb43 cppgc::internal::PersistentRegionLock::AssertLocked() + 63795
8   Electron Framework            	       0x10fe0f49d cppgc::internal::PersistentRegionLock::AssertLocked() + 62093
9   Electron Framework            	       0x10fe0f35e cppgc::internal::PersistentRegionLock::AssertLocked() + 61774
10  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
11  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
12  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 20:: CompositorTileWorker1
0   libsystem_kernel.dylib        	    0x7ff8162df70e __psynch_cvwait + 10
1   libsystem_pthread.dylib       	    0x7ff8163211f7 _pthread_cond_wait + 994
2   Electron Framework            	       0x11052930b sk_X509_call_free_func + 165339
3   Electron Framework            	       0x110a8e64e v8::internal::OptimizingCompileTaskExecutor::RunCompilationJob(v8::internal::OptimizingCompileTaskState&, v8::internal::Isolate*, v8::internal::LocalIsolate&, v8::internal::TurbofanCompilationJob*) + 415854
4   Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
5   libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
6   libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 21:
0   libsystem_kernel.dylib        	    0x7ff8162e388a poll + 10
1   Electron Framework            	       0x1129d4669 node::PrincipalRealm::tick_callback_function() const + 945465
2   Electron Framework            	       0x112962c7b node::PrincipalRealm::tick_callback_function() const + 480075
3   libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
4   libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 22:: NetworkNotificationThreadMac
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   CoreFoundation                	    0x7ff8163dc2bf __CFRunLoopServiceMachPort + 145
5   CoreFoundation                	    0x7ff8163dad58 __CFRunLoopRun + 1354
6   CoreFoundation                	    0x7ff8164a28b7 _CFRunLoopRunSpecificWithOptions + 496
7   Foundation                    	    0x7ff817645c87 -[NSRunLoop(NSRunLoop) runMode:beforeDate:] + 216
8   Electron Framework            	       0x11188c389 node::PrincipalRealm::get_source_map_error_source() const + 76377
9   Electron Framework            	       0x11188c222 node::PrincipalRealm::get_source_map_error_source() const + 76018
10  Electron Framework            	       0x110c517f9 v8::Isolate::Allocate() + 74777
11  Electron Framework            	       0x110f62fc1 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1777
12  Electron Framework            	       0x110f62ee8 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1560
13  Electron Framework            	       0x115b22d2d ares_dns_rr_get_ttl + 4007037
14  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
15  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
16  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 23:: ThreadPoolSingleThreadSharedBackgroundBlocking1
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   Electron Framework            	       0x1101c9db1 node::PrincipalRealm::inspector_disable_async_hooks() const + 79121
5   Electron Framework            	       0x1101c9b9a node::PrincipalRealm::inspector_disable_async_hooks() const + 78586
6   Electron Framework            	       0x1101c9b44 node::PrincipalRealm::inspector_disable_async_hooks() const + 78500
7   Electron Framework            	       0x10fe0fb43 cppgc::internal::PersistentRegionLock::AssertLocked() + 63795
8   Electron Framework            	       0x10fe1018d cppgc::internal::PersistentRegionLock::AssertLocked() + 65405
9   Electron Framework            	       0x10fe0f36d cppgc::internal::PersistentRegionLock::AssertLocked() + 61789
10  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
11  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
12  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 24:: ThreadPoolForegroundWorker
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   Electron Framework            	       0x1101c9db1 node::PrincipalRealm::inspector_disable_async_hooks() const + 79121
5   Electron Framework            	       0x1101c9b9a node::PrincipalRealm::inspector_disable_async_hooks() const + 78586
6   Electron Framework            	       0x1101c9b44 node::PrincipalRealm::inspector_disable_async_hooks() const + 78500
7   Electron Framework            	       0x10fe0fb43 cppgc::internal::PersistentRegionLock::AssertLocked() + 63795
8   Electron Framework            	       0x10fe0f40d cppgc::internal::PersistentRegionLock::AssertLocked() + 61949
9   Electron Framework            	       0x10fe0f317 cppgc::internal::PersistentRegionLock::AssertLocked() + 61703
10  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
11  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
12  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 25:: NetworkConfigWatcher
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   CoreFoundation                	    0x7ff8163dc2bf __CFRunLoopServiceMachPort + 145
5   CoreFoundation                	    0x7ff8163dad58 __CFRunLoopRun + 1354
6   CoreFoundation                	    0x7ff8164a28b7 _CFRunLoopRunSpecificWithOptions + 496
7   Foundation                    	    0x7ff817645c87 -[NSRunLoop(NSRunLoop) runMode:beforeDate:] + 216
8   Electron Framework            	       0x11188c389 node::PrincipalRealm::get_source_map_error_source() const + 76377
9   Electron Framework            	       0x11188c222 node::PrincipalRealm::get_source_map_error_source() const + 76018
10  Electron Framework            	       0x110c517f9 v8::Isolate::Allocate() + 74777
11  Electron Framework            	       0x110f62fc1 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1777
12  Electron Framework            	       0x110f62ee8 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1560
13  Electron Framework            	       0x115b22d2d ares_dns_rr_get_ttl + 4007037
14  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
15  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
16  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 26:: ThreadPoolBackgroundWorker
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   Electron Framework            	       0x1101c9db1 node::PrincipalRealm::inspector_disable_async_hooks() const + 79121
5   Electron Framework            	       0x1101c9b9a node::PrincipalRealm::inspector_disable_async_hooks() const + 78586
6   Electron Framework            	       0x1101c9b44 node::PrincipalRealm::inspector_disable_async_hooks() const + 78500
7   Electron Framework            	       0x10fe0fb43 cppgc::internal::PersistentRegionLock::AssertLocked() + 63795
8   Electron Framework            	       0x10fe0f43d cppgc::internal::PersistentRegionLock::AssertLocked() + 61997
9   Electron Framework            	       0x10fe0f340 cppgc::internal::PersistentRegionLock::AssertLocked() + 61744
10  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
11  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
12  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 27:: ThreadPoolSingleThreadSharedForeground2
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   Electron Framework            	       0x1101c9db1 node::PrincipalRealm::inspector_disable_async_hooks() const + 79121
5   Electron Framework            	       0x1101c9b9a node::PrincipalRealm::inspector_disable_async_hooks() const + 78586
6   Electron Framework            	       0x1101c9b44 node::PrincipalRealm::inspector_disable_async_hooks() const + 78500
7   Electron Framework            	       0x10fe0fb43 cppgc::internal::PersistentRegionLock::AssertLocked() + 63795
8   Electron Framework            	       0x10fe0f46d cppgc::internal::PersistentRegionLock::AssertLocked() + 62045
9   Electron Framework            	       0x10fe0f34f cppgc::internal::PersistentRegionLock::AssertLocked() + 61759
10  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
11  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
12  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 28:: ThreadPoolForegroundWorker
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   Electron Framework            	       0x1101c9db1 node::PrincipalRealm::inspector_disable_async_hooks() const + 79121
5   Electron Framework            	       0x1101c9b9a node::PrincipalRealm::inspector_disable_async_hooks() const + 78586
6   Electron Framework            	       0x1101c9b44 node::PrincipalRealm::inspector_disable_async_hooks() const + 78500
7   Electron Framework            	       0x10fe0fb43 cppgc::internal::PersistentRegionLock::AssertLocked() + 63795
8   Electron Framework            	       0x10fe0f40d cppgc::internal::PersistentRegionLock::AssertLocked() + 61949
9   Electron Framework            	       0x10fe0f317 cppgc::internal::PersistentRegionLock::AssertLocked() + 61703
10  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
11  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
12  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 29:: ThreadPoolSingleThreadSharedForegroundBlocking3
0   libsystem_kernel.dylib        	    0x7ff8162dcb4e mach_msg2_trap + 10
1   libsystem_kernel.dylib        	    0x7ff8162eaf49 mach_msg2_internal + 83
2   libsystem_kernel.dylib        	    0x7ff8162e3b64 mach_msg_overwrite + 586
3   libsystem_kernel.dylib        	    0x7ff8162dce54 mach_msg + 19
4   Electron Framework            	       0x1101c9db1 node::PrincipalRealm::inspector_disable_async_hooks() const + 79121
5   Electron Framework            	       0x1101c9b9a node::PrincipalRealm::inspector_disable_async_hooks() const + 78586
6   Electron Framework            	       0x1101c9b44 node::PrincipalRealm::inspector_disable_async_hooks() const + 78500
7   Electron Framework            	       0x10fe0fb43 cppgc::internal::PersistentRegionLock::AssertLocked() + 63795
8   Electron Framework            	       0x10fe0f46d cppgc::internal::PersistentRegionLock::AssertLocked() + 62045
9   Electron Framework            	       0x10fe0f34f cppgc::internal::PersistentRegionLock::AssertLocked() + 61759
10  Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
11  libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
12  libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 30:: CacheThread_BlockFile
0   libsystem_kernel.dylib        	    0x7ff8162e64ca kevent64 + 10
1   Electron Framework            	       0x10ff09534 node::PrincipalRealm::snapshot_serialize_callback() const + 56548
2   Electron Framework            	       0x110c517f9 v8::Isolate::Allocate() + 74777
3   Electron Framework            	       0x110f62fc1 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1777
4   Electron Framework            	       0x110f62ee8 v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>) + 1560
5   Electron Framework            	       0x115b22d2d ares_dns_rr_get_ttl + 4007037
6   Electron Framework            	       0x1107097c6 node::PrincipalRealm::enhance_fatal_stack_before_inspector() const + 16166
7   libsystem_pthread.dylib       	    0x7ff816320d49 _pthread_start + 115
8   libsystem_pthread.dylib       	    0x7ff81631c82f thread_start + 15

Thread 31:

Thread 32:

Thread 33:

Thread 0 crashed with X86 Thread State (64-bit):
  rax: 0x00007ff7bc2ea9e8  rbx: 0x0000000000000000  rcx: 0x0000000000000008  rdx: 0x000000011b553110
  rdi: 0x00007ff7bc2ea9e8  rsi: 0x0000000000001000  rbp: 0x00007ff7bc2ea9f0  rsp: 0x00007ff7bc2ea9e0
   r8: 0x0000011800000000   r9: 0x0000000000000600  r10: 0x0000010725a9f600  r11: 0x0000011c028c36f0
  r12: 0x00007ff7bc2eab75  r13: 0x000000011a577c00  r14: 0x000000011a081948  r15: 0x0000000000000004
  rip: 0x0000000115b4c8af  rfl: 0x0000000000000202  cr2: 0x0000000000000000
  
Logical CPU:     6
Error Code:      0x00000000 
Trap Number:     3

Binary Images:
       0x103c10000 -        0x103c11fff com.kamp.app (1.12.0) <4c4c4466-5555-3144-a187-be5fe5df241d> /Applications/Kamp.app/Contents/MacOS/Kamp
       0x10fc91000 -        0x11aeaffff com.github.Electron.framework (*) <4c4c44dc-5555-3144-a15b-f53dfd847e46> /Applications/Kamp.app/Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework
       0x103c3f000 -        0x103c54fff com.github.Squirrel (1.0) <4c4c445c-5555-3144-a13f-ab997eb52e18> /Applications/Kamp.app/Contents/Frameworks/Squirrel.framework/Versions/A/Squirrel
       0x103cbf000 -        0x103d02fff com.electron.reactive (3.1.0) <4c4c44b9-5555-3144-a174-05ca8f2d2a08> /Applications/Kamp.app/Contents/Frameworks/ReactiveObjC.framework/Versions/A/ReactiveObjC
       0x103c62000 -        0x103c6dfff org.mantle.Mantle (1.0) <4c4c44cc-5555-3144-a164-58db4dcd453d> /Applications/Kamp.app/Contents/Frameworks/Mantle.framework/Versions/A/Mantle
       0x103f71000 -        0x10417bfff libffmpeg.dylib (*) <4c4c448a-5555-3144-a162-14920b52fb22> /Applications/Kamp.app/Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libffmpeg.dylib
       0x104415000 -        0x104421fff libobjc-trampolines.dylib (*) <e8581c0d-cb70-323f-90a9-0d16881957e4> /usr/lib/libobjc-trampolines.dylib
               0x0 - 0xffffffffffffffff ??? (*) <00000000-0000-0000-0000-000000000000> ???
    0x7ff816360000 -     0x7ff81681c704 com.apple.CoreFoundation (6.9) <9da51ee2-cd79-3042-a96e-e0b9bd693182> /System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation
    0x7ff82306c000 -     0x7ff823348b4e com.apple.HIToolbox (2.1.1) <d03815ad-d064-3912-ae75-d4f7f4748b0a> /System/Library/Frameworks/Carbon.framework/Versions/A/Frameworks/HIToolbox.framework/Versions/A/HIToolbox
    0x7ff81a20f000 -     0x7ff81ba0b0d6 com.apple.AppKit (6.9) <862ab35d-3dcb-3d7a-bc21-e10207851479> /System/Library/Frameworks/AppKit.framework/Versions/C/AppKit
    0x7ff815f38000 -     0x7ff815fd559f dyld (*) <a58aa73b-6617-3a28-ac72-a8a5afd06772> /usr/lib/dyld
    0x7ff8162dc000 -     0x7ff81631a567 libsystem_kernel.dylib (*) <fafba22b-d2aa-3fdb-b4e1-451dfe00694e> /usr/lib/system/libsystem_kernel.dylib
    0x7ff81631b000 -     0x7ff816326ebf libsystem_pthread.dylib (*) <1b15ae36-b1c1-36bd-94cf-3c230733223f> /usr/lib/system/libsystem_pthread.dylib
    0x7ff8175eb000 -     0x7ff818624b5f com.apple.Foundation (6.9) <af3fb30d-e907-35d3-8108-44ebc9a3e60b> /System/Library/Frameworks/Foundation.framework/Versions/C/Foundation

External Modification Summary:
  Calls made by other processes targeting this process:
    task_for_pid: 0
    thread_create: 0
    thread_set_state: 0
  Calls made by this process:
    task_for_pid: 0
    thread_create: 0
    thread_set_state: 0
  Calls made by all processes on this machine:
    task_for_pid: 0
    thread_create: 0
    thread_set_state: 0

VM Region Summary:
ReadOnly portion of Libraries: Total=1.6G resident=0K(0%) swapped_out_or_unallocated=1.6G(100%)
Writable regions: Total=1.8G written=0K(0%) resident=0K(0%) swapped_out=0K(0%) unallocated=1.8G(100%)

                                VIRTUAL   REGION 
REGION TYPE                        SIZE    COUNT (non-coalesced) 
===========                     =======  ======= 
Accelerate framework               128K        1 
Activity Tracing                   256K        1 
AttributeGraph Data               1024K        1 
ColorSync                            8K        2 
CoreAnimation                      244K       24 
CoreGraphics                        12K        2 
CoreUI image data                  704K        5 
Dispatch continuations           128.0M        1 
Foundation                          36K        2 
Kernel Alloc Once                  208K        3 
MALLOC                           160.8M       81 
MALLOC guard page                   96K       24 
Mach message                        32K        6 
Memory Tag 253                    48.7G     5111 
Memory Tag 255                     1.3T      540 
Memory Tag 255 (reserved)          384K        6         reserved VM address space (unallocated)
PROTECTED_MEMORY                     4K        1 
STACK GUARD                       56.1M       34 
Stack                            226.3M       35 
VM_ALLOCATE                      415.9M       52 
__CTF                               824        1 
__DATA                            46.3M     1007 
__DATA_CONST                     130.0M     1056 
__DATA_DIRTY                      8165K      886 
__FONT_DATA                        2352        1 
__LINKEDIT                       157.0M        9 
__OBJC_RO                         65.1M        1 
__OBJC_RW                         2596K        3 
__TEXT                             1.4G     1074 
__TPRO_CONST                         16        2 
mapped file                      286.0M       64 
shared memory                     1320K       20 
===========                     =======  ======= 
TOTAL                              1.4T    10056 
TOTAL, minus reserved VM space     1.4T    10056 

-----------
Full Report
-----------

{"app_name":"Kamp","timestamp":"2026-04-25 10:31:33.00 -0400","app_version":"1.12.0","slice_uuid":"4c4c4466-5555-3144-a187-be5fe5df241d","build_version":"1.12.0","platform":1,"bundleID":"com.kamp.app","share_with_app_devs":1,"is_first_party":0,"bug_type":"309","os_version":"macOS 26.4.1 (25E253)","roots_installed":0,"name":"Kamp","incident_id":"77F91CF7-A5BF-4C64-B880-BAF310F8C478"}
{
  "uptime" : 36000,
  "procRole" : "Foreground",
  "version" : 2,
  "userID" : 501,
  "deployVersion" : 210,
  "modelCode" : "MacBookPro16,1",
  "coalitionID" : 13381,
  "osVersion" : {
    "train" : "macOS 26.4.1",
    "build" : "25E253",
    "releaseType" : "User"
  },
  "captureTime" : "2026-04-25 10:31:11.4108 -0400",
  "codeSigningMonitor" : 0,
  "incident" : "77F91CF7-A5BF-4C64-B880-BAF310F8C478",
  "pid" : 18522,
  "cpuType" : "X86-64",
  "procLaunch" : "2026-04-25 10:27:06.8880 -0400",
  "procStartAbsTime" : 35842764866900,
  "procExitAbsTime" : 36087282136644,
  "procName" : "Kamp",
  "procPath" : "\/Applications\/Kamp.app\/Contents\/MacOS\/Kamp",
  "bundleInfo" : {"CFBundleShortVersionString":"1.12.0","CFBundleVersion":"1.12.0","CFBundleIdentifier":"com.kamp.app"},
  "storeInfo" : {"deviceIdentifierForVendor":"3099F5AE-D29C-5026-9D91-FF215D367FE0","thirdParty":true},
  "parentProc" : "launchd",
  "parentPid" : 1,
  "coalitionName" : "com.kamp.app",
  "crashReporterKey" : "456A5EAC-AA57-92DE-2142-B1399DA39396",
  "appleIntelligenceStatus" : {"state":"unavailable","reasons":["deviceNotCapable"]},
  "developerMode" : 1,
  "codeSigningID" : "com.kamp.app",
  "codeSigningTeamID" : "X6K4L8ZMLS",
  "codeSigningFlags" : 570491649,
  "codeSigningValidationCategory" : 6,
  "codeSigningTrustLevel" : 4294967295,
  "codeSigningAuxiliaryInfo" : 0,
  "bootSessionUUID" : "DEEBB477-2E7F-478C-886A-BA9C16D74842",
  "wakeTime" : 12040,
  "bridgeVersion" : {"build":"23P4242","train":"10.4"},
  "sleepWakeUUID" : "2643419C-D035-43EE-886F-755BAAA1F190",
  "sip" : "enabled",
  "exception" : {"codes":"0x0000000000000002, 0x0000000000000000","rawCodes":[2,0],"type":"EXC_BREAKPOINT","signal":"SIGTRAP"},
  "termination" : {"flags":0,"code":5,"namespace":"SIGNAL","indicator":"Trace\/BPT trap: 5","byProc":"exc handler","byPid":18522},
  "os_fault" : {"process":"Kamp"},
  "extMods" : {"caller":{"thread_create":0,"thread_set_state":0,"task_for_pid":0},"system":{"thread_create":0,"thread_set_state":0,"task_for_pid":0},"targeted":{"thread_create":0,"thread_set_state":0,"task_for_pid":0},"warnings":0},
  "faultingThread" : 0,
  "threads" : [{"queue":"com.apple.main-thread","instructionState":{"instructionStream":{"bytes":[0,85,72,137,229,83,80,72,137,251,72,137,117,240,232,40,215,168,3,132,192,117,8,72,137,223,232,210,4,53,250,72,141,123,8,72,141,117,240,232,229,75,153,253,72,137,223,232,1,215,168,3,72,131,196,8,91,93,195,102,15,31,68,0,0,85,72,137,229,72,131,236,16,72,137,61,177,124,187,5,72,141,69,248,72,137,56,72,137,199,232,2,200,20,250,204,15,11,102,102,102,102,102,102,46,15,31,132,0,0,0,0,0,85,72,137,229,232,199,255,255,255,15,31,128,0,0,0,0,85,72,137,229,83,80,72,137,251,232,34,0,0,0,72,137,223,232,218,255,255,255,102,46,15,31,132,0,0,0,0,0,85,72,137,229,232,7,0,0,0,49,255,232,192,255,255,255,85,72,137,229,72,139,5,157,144,173,5,72,133,192,116],"offset":96}},"frames":[{"imageOffset":99334319,"symbol":"ares_dns_rr_get_ttl","symbolLocation":4177919,"imageIndex":1},{"imageOffset":99334345,"symbol":"ares_dns_rr_get_ttl","symbolLocation":4177945,"imageIndex":1},{"imageOffset":99334374,"symbol":"ares_dns_rr_get_ttl","symbolLocation":4177974,"imageIndex":1},{"imageOffset":46993479,"symbol":"node::PrincipalRealm::tick_callback_function() const","symbolLocation":476951,"imageIndex":1},{"imageOffset":66227687,"symbol":"v8::Isolate::Free(v8::Isolate*)","symbolLocation":7143031,"imageIndex":1},{"imageOffset":66227590,"symbol":"v8::Isolate::Free(v8::Isolate*)","symbolLocation":7142934,"imageIndex":1},{"imageOffset":1524152,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":20392,"imageIndex":1},{"imageOffset":14128324,"symbol":"rust_png$cxxbridge1$194$Reader$width","symbolLocation":125860,"imageIndex":1},{"imageOffset":25071698,"symbol":"node::BaseObject::TransferForMessaging()","symbolLocation":36738,"imageIndex":1},{"imageOffset":16055018,"symbol":"v8::internal::OptimizingCompileTaskExecutor::WaitUntilCompilationJobsDoneForIsolate(v8::internal::Isolate*)","symbolLocation":32330,"imageIndex":1},{"imageOffset":5970970,"symbol":"v8::internal::ThreadIsolation::LookupJitAllocation(unsigned long, unsigned long, v8::internal::ThreadIsolation::JitAllocationType, bool)","symbolLocation":57642,"imageIndex":1},{"imageOffset":5969669,"symbol":"v8::internal::ThreadIsolation::LookupJitAllocation(unsigned long, unsigned long, v8::internal::ThreadIsolation::JitAllocationType, bool)","symbolLocation":56341,"imageIndex":1},{"imageOffset":76035254,"symbol":"_v8_internal_Node_Print(void*)","symbolLocation":3922166,"imageIndex":1},{"imageOffset":6246203731,"imageIndex":7},{"imageOffset":75590317,"symbol":"_v8_internal_Node_Print(void*)","symbolLocation":3477229,"imageIndex":1},{"imageOffset":76564014,"symbol":"_v8_internal_Node_Print(void*)","symbolLocation":4450926,"imageIndex":1},{"imageOffset":75514654,"symbol":"_v8_internal_Node_Print(void*)","symbolLocation":3401566,"imageIndex":1},{"imageOffset":75297259,"symbol":"_v8_internal_Node_Print(void*)","symbolLocation":3184171,"imageIndex":1},{"imageOffset":17940646,"symbol":"v8::internal::ThreadIsolation::RegisterJitPage(unsigned long, unsigned long)","symbolLocation":185558,"imageIndex":1},{"imageOffset":500938,"symbol":"v8::Isolate::SuppressMicrotaskExecutionScope::SuppressMicrotaskExecutionScope(v8::Isolate*, v8::MicrotaskQueue*)","symbolLocation":250,"imageIndex":1},{"imageOffset":500843,"symbol":"v8::Isolate::SuppressMicrotaskExecutionScope::SuppressMicrotaskExecutionScope(v8::Isolate*, v8::MicrotaskQueue*)","symbolLocation":155,"imageIndex":1},{"imageOffset":499949,"symbol":"cppgc::internal::TraceTraitFromInnerAddressImpl::GetTraceDescriptor(void const*)","symbolLocation":1021,"imageIndex":1},{"imageOffset":47753512,"symbol":"node::InternalCallbackScope::Close()","symbolLocation":328,"imageIndex":1},{"imageOffset":47752081,"symbol":"node::CallbackScope::~CallbackScope()","symbolLocation":49,"imageIndex":1},{"imageOffset":46387094,"symbol":"node::crypto::TLSWrap::GetFD()","symbolLocation":1058438,"imageIndex":1},{"imageOffset":43157141,"symbol":"v8::internal::compiler::CompilationDependencies::DependOnContextCell(v8::internal::compiler::ContextRef, unsigned long, v8::internal::ContextCell::State, v8::internal::compiler::JSHeapBroker*)","symbolLocation":591973,"imageIndex":1},{"imageOffset":358965,"symbol":"v8::Promise::Resolver::GetPromise()","symbolLocation":10389,"imageIndex":1},{"imageOffset":10350236,"symbol":"v8::FunctionTemplate::New(v8::Isolate*, void (*)(v8::FunctionCallbackInfo<v8::Value> const&), v8::Local<v8::Value>, v8::Local<v8::Signature>, int, v8::ConstructorBehavior, v8::SideEffectType, v8::CFunction const*, unsigned short, unsigned short, unsigned short)","symbolLocation":37596,"imageIndex":1},{"imageOffset":1828474,"symbol":"v8::internal::compiler::CompilationDependencies::FieldRepresentationDependencyOffTheRecord(v8::internal::compiler::MapRef, v8::internal::compiler::MapRef, v8::internal::InternalIndex, v8::internal::Representation) const","symbolLocation":211658,"imageIndex":1},{"imageOffset":43195103,"symbol":"v8::internal::compiler::CompilationDependencies::DependOnContextCell(v8::internal::compiler::ContextRef, unsigned long, v8::internal::ContextCell::State, v8::internal::compiler::JSHeapBroker*)","symbolLocation":629935,"imageIndex":1},{"imageOffset":508296,"symbol":"__CFRUNLOOP_IS_CALLING_OUT_TO_A_SOURCE0_PERFORM_FUNCTION__","symbolLocation":17,"imageIndex":8},{"imageOffset":508202,"symbol":"__CFRunLoopDoSource0","symbolLocation":157,"imageIndex":8},{"imageOffset":507626,"symbol":"__CFRunLoopDoSources0","symbolLocation":203,"imageIndex":8},{"imageOffset":502690,"symbol":"__CFRunLoopRun","symbolLocation":916,"imageIndex":8},{"imageOffset":1321143,"symbol":"_CFRunLoopRunSpecificWithOptions","symbolLocation":496,"imageIndex":8},{"imageOffset":678795,"symbol":"RunCurrentEventLoopInMode","symbolLocation":283,"imageIndex":9},{"imageOffset":691277,"symbol":"ReceiveNextEventCommon","symbolLocation":599,"imageIndex":9},{"imageOffset":2298042,"symbol":"_BlockUntilNextEventMatchingListInMode","symbolLocation":37,"imageIndex":9},{"imageOffset":7634228,"symbol":"_DPSBlockUntilNextEventMatchingListInMode","symbolLocation":172,"imageIndex":10},{"imageOffset":252373,"symbol":"_DPSNextEvent","symbolLocation":800,"imageIndex":10},{"imageOffset":13548371,"symbol":"-[NSApplication(NSEventRouting) _nextEventMatchingEventMask:untilDate:inMode:dequeue:]","symbolLocation":1265,"imageIndex":10},{"imageOffset":13547049,"symbol":"-[NSApplication(NSEventRouting) nextEventMatchingMask:untilDate:inMode:dequeue:]","symbolLocation":67,"imageIndex":10},{"imageOffset":190568,"symbol":"-[NSApplication run]","symbolLocation":472,"imageIndex":10},{"imageOffset":34640136,"symbol":"v8::internal::ThreadIsolation::UnregisterWasmAllocation(unsigned long, unsigned long)","symbolLocation":12040,"imageIndex":1},{"imageOffset":29340194,"symbol":"node::PrincipalRealm::get_source_map_error_source() const","symbolLocation":76018,"imageIndex":1},{"imageOffset":16517113,"symbol":"v8::Isolate::Allocate()","symbolLocation":74777,"imageIndex":1},{"imageOffset":19734465,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1777,"imageIndex":1},{"imageOffset":31064663,"symbol":"node::PrincipalRealm::http2session_on_headers_function() const","symbolLocation":31863,"imageIndex":1},{"imageOffset":31064482,"symbol":"node::PrincipalRealm::http2session_on_headers_function() const","symbolLocation":31682,"imageIndex":1},{"imageOffset":31798220,"symbol":"v8::Isolate::RequestInterrupt(void (*)(v8::Isolate*, void*), void*)","symbolLocation":281244,"imageIndex":1},{"imageOffset":52856618,"symbol":"v8::CpuProfile::GetSamplesCount() const","symbolLocation":1149242,"imageIndex":1},{"imageOffset":52860815,"symbol":"v8::CpuProfile::GetSamplesCount() const","symbolLocation":1153439,"imageIndex":1},{"imageOffset":52860269,"symbol":"v8::CpuProfile::GetSamplesCount() const","symbolLocation":1152893,"imageIndex":1},{"imageOffset":29422609,"symbol":"node::PrincipalRealm::get_source_map_error_source() const","symbolLocation":158433,"imageIndex":1},{"imageOffset":29420896,"symbol":"node::PrincipalRealm::get_source_map_error_source() const","symbolLocation":156720,"imageIndex":1},{"imageOffset":45176740,"symbol":"ElectronMain","symbolLocation":132,"imageIndex":1},{"imageOffset":76728,"symbol":"start","symbolLocation":3240,"imageIndex":11}],"id":589439,"triggered":true,"threadState":{"r13":{"value":4736908288,"symbolLocation":86964,"symbol":"v8::HeapProfiler::kUnknownObjectId"},"rax":{"value":140701990824424},"rflags":{"value":514},"cpu":{"value":6},"r14":{"value":4731705672},"rsi":{"value":4096},"r8":{"value":1202590842880},"cr2":{"value":0},"rdx":{"value":4753535248,"symbolLocation":90472,"symbol":"v8_inspector::protocol::Debugger::API::Paused::ReasonEnum::Step"},"r10":{"value":1130208294400},"r9":{"value":1536},"r15":{"value":4},"rbx":{"value":0},"trap":{"value":3},"err":{"value":0},"r11":{"value":1219813455600},"rip":{"value":4659136687,"matchesCrashFrame":1},"rbp":{"value":140701990824432},"rsp":{"value":140701990824416},"r12":{"value":140701990824821},"rcx":{"value":8},"flavor":"x86_THREAD_STATE","rdi":{"value":140701990824424}},"name":"CrBrowserMain"},{"id":589456,"name":"com.apple.NSEventThread","threadState":{"r13":{"value":21592279046},"rax":{"value":268451845},"rflags":{"value":518},"cpu":{"value":0},"r14":{"value":2},"rsi":{"value":21592279046},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":8589934592},"r10":{"value":100068443029504},"r9":{"value":100068443029504},"r15":{"value":0},"rbx":{"value":123145463197840},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":518},"rip":{"value":140703500716878},"rbp":{"value":123145463197680},"rsp":{"value":123145463197576},"r12":{"value":100068443029504},"rcx":{"value":123145463197576},"flavor":"x86_THREAD_STATE","rdi":{"value":123145463197840}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":508607,"symbol":"__CFRunLoopServiceMachPort","symbolLocation":145,"imageIndex":8},{"imageOffset":503128,"symbol":"__CFRunLoopRun","symbolLocation":1354,"imageIndex":8},{"imageOffset":1321143,"symbol":"_CFRunLoopRunSpecificWithOptions","symbolLocation":496,"imageIndex":8},{"imageOffset":1636000,"symbol":"_NSEventThread","symbolLocation":158,"imageIndex":10},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589457,"name":"PerfettoTrace","threadState":{"r13":{"value":1202593764128},"rax":{"value":4},"rflags":{"value":583},"cpu":{"value":0},"r14":{"value":1202591385472},"rsi":{"value":0},"r8":{"value":1},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":1202592993952},"r9":{"value":0},"r15":{"value":1202591385736},"rbx":{"value":123145471602088},"trap":{"value":133},"err":{"value":33554801},"r11":{"value":582},"rip":{"value":140703500756170},"rbp":{"value":123145471602160},"rsp":{"value":123145471601960},"r12":{"value":2147483648},"rcx":{"value":123145471601960},"flavor":"x86_THREAD_STATE","rdi":{"value":5}},"frames":[{"imageOffset":42186,"symbol":"kevent64","symbolLocation":10,"imageIndex":12},{"imageOffset":2590004,"symbol":"node::PrincipalRealm::snapshot_serialize_callback() const","symbolLocation":56548,"imageIndex":1},{"imageOffset":16517113,"symbol":"v8::Isolate::Allocate()","symbolLocation":74777,"imageIndex":1},{"imageOffset":19734465,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1777,"imageIndex":1},{"imageOffset":19734248,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1560,"imageIndex":1},{"imageOffset":99163437,"symbol":"ares_dns_rr_get_ttl","symbolLocation":4007037,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589458,"name":"ThreadPoolServiceThread","threadState":{"r13":{"value":1202593765664},"rax":{"value":4},"rflags":{"value":583},"cpu":{"value":0},"r14":{"value":1202591399680},"rsi":{"value":0},"r8":{"value":3},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":1219771027152},"r9":{"value":0},"r15":{"value":1202591399944},"rbx":{"value":123145480002952},"trap":{"value":133},"err":{"value":33554801},"r11":{"value":582},"rip":{"value":140703500756170},"rbp":{"value":123145480003024},"rsp":{"value":123145480002824},"r12":{"value":2147483648},"rcx":{"value":123145480002824},"flavor":"x86_THREAD_STATE","rdi":{"value":6}},"frames":[{"imageOffset":42186,"symbol":"kevent64","symbolLocation":10,"imageIndex":12},{"imageOffset":2590004,"symbol":"node::PrincipalRealm::snapshot_serialize_callback() const","symbolLocation":56548,"imageIndex":1},{"imageOffset":16517113,"symbol":"v8::Isolate::Allocate()","symbolLocation":74777,"imageIndex":1},{"imageOffset":19734465,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1777,"imageIndex":1},{"imageOffset":19734248,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1560,"imageIndex":1},{"imageOffset":19734157,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1469,"imageIndex":1},{"imageOffset":99163437,"symbol":"ares_dns_rr_get_ttl","symbolLocation":4007037,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589459,"name":"ThreadPoolForegroundWorker","threadState":{"r13":{"value":17179869186},"rax":{"value":268451845},"rflags":{"value":514},"cpu":{"value":0},"r14":{"value":32},"rsi":{"value":17179869186},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":158342559301632},"r9":{"value":158342559301632},"r15":{"value":0},"rbx":{"value":123145488403696},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":514},"rip":{"value":140703500716878},"rbp":{"value":123145488403024},"rsp":{"value":123145488402920},"r12":{"value":158342559301632},"rcx":{"value":123145488402920},"flavor":"x86_THREAD_STATE","rdi":{"value":123145488403696}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":5475761,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":79121,"imageIndex":1},{"imageOffset":5475226,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78586,"imageIndex":1},{"imageOffset":5475140,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78500,"imageIndex":1},{"imageOffset":1567555,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":63795,"imageIndex":1},{"imageOffset":1565709,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61949,"imageIndex":1},{"imageOffset":1565463,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61703,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589460,"name":"ThreadPoolBackgroundWorker","threadState":{"r13":{"value":17179869186},"rax":{"value":268451845},"rflags":{"value":514},"cpu":{"value":0},"r14":{"value":32},"rsi":{"value":17179869186},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":160541582557184},"r9":{"value":160541582557184},"r15":{"value":0},"rbx":{"value":123145496804592},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":514},"rip":{"value":140703500716878},"rbp":{"value":123145496803920},"rsp":{"value":123145496803816},"r12":{"value":160541582557184},"rcx":{"value":123145496803816},"flavor":"x86_THREAD_STATE","rdi":{"value":123145496804592}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":5475761,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":79121,"imageIndex":1},{"imageOffset":5475226,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78586,"imageIndex":1},{"imageOffset":5475140,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78500,"imageIndex":1},{"imageOffset":1567555,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":63795,"imageIndex":1},{"imageOffset":1565757,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61997,"imageIndex":1},{"imageOffset":1565504,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61744,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589461,"name":"ThreadPoolForegroundWorker","threadState":{"r13":{"value":17179869186},"rax":{"value":268451845},"rflags":{"value":514},"cpu":{"value":0},"r14":{"value":32},"rsi":{"value":17179869186},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":168238163951616},"r9":{"value":168238163951616},"r15":{"value":0},"rbx":{"value":123145505205488},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":514},"rip":{"value":140703500716878},"rbp":{"value":123145505204816},"rsp":{"value":123145505204712},"r12":{"value":168238163951616},"rcx":{"value":123145505204712},"flavor":"x86_THREAD_STATE","rdi":{"value":123145505205488}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":5475761,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":79121,"imageIndex":1},{"imageOffset":5475226,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78586,"imageIndex":1},{"imageOffset":5475140,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78500,"imageIndex":1},{"imageOffset":1567555,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":63795,"imageIndex":1},{"imageOffset":1565709,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61949,"imageIndex":1},{"imageOffset":1565463,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61703,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589462,"name":"Chrome_IOThread","threadState":{"r13":{"value":1202593764896},"rax":{"value":4},"rflags":{"value":583},"cpu":{"value":0},"r14":{"value":1219770731264},"rsi":{"value":0},"r8":{"value":8},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":1219786149056},"r9":{"value":0},"r15":{"value":1219770731528},"rbx":{"value":123145513606504},"trap":{"value":133},"err":{"value":33554801},"r11":{"value":582},"rip":{"value":140703500756170},"rbp":{"value":123145513606576},"rsp":{"value":123145513606376},"r12":{"value":2147483648},"rcx":{"value":123145513606376},"flavor":"x86_THREAD_STATE","rdi":{"value":7}},"frames":[{"imageOffset":42186,"symbol":"kevent64","symbolLocation":10,"imageIndex":12},{"imageOffset":2590004,"symbol":"node::PrincipalRealm::snapshot_serialize_callback() const","symbolLocation":56548,"imageIndex":1},{"imageOffset":16517113,"symbol":"v8::Isolate::Allocate()","symbolLocation":74777,"imageIndex":1},{"imageOffset":19734465,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1777,"imageIndex":1},{"imageOffset":19734248,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1560,"imageIndex":1},{"imageOffset":29153815,"symbol":"v8::internal::OptimizingCompileTaskExecutor::OptimizingCompileTaskExecutor()","symbolLocation":75447,"imageIndex":1},{"imageOffset":99163437,"symbol":"ares_dns_rr_get_ttl","symbolLocation":4007037,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589463,"name":"MemoryInfra","threadState":{"r13":{"value":17179869186},"rax":{"value":268451845},"rflags":{"value":514},"cpu":{"value":0},"r14":{"value":32},"rsi":{"value":17179869186},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":274890791845888},"r9":{"value":274890791845888},"r15":{"value":0},"rbx":{"value":123145522007120},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":514},"rip":{"value":140703500716878},"rbp":{"value":123145522006448},"rsp":{"value":123145522006344},"r12":{"value":274890791845888},"rcx":{"value":123145522006344},"flavor":"x86_THREAD_STATE","rdi":{"value":123145522007120}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":5475761,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":79121,"imageIndex":1},{"imageOffset":5475226,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78586,"imageIndex":1},{"imageOffset":16511814,"symbol":"v8::Isolate::Allocate()","symbolLocation":69478,"imageIndex":1},{"imageOffset":16511426,"symbol":"v8::Isolate::Allocate()","symbolLocation":69090,"imageIndex":1},{"imageOffset":16517113,"symbol":"v8::Isolate::Allocate()","symbolLocation":74777,"imageIndex":1},{"imageOffset":19734465,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1777,"imageIndex":1},{"imageOffset":19734248,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1560,"imageIndex":1},{"imageOffset":99163437,"symbol":"ares_dns_rr_get_ttl","symbolLocation":4007037,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589464,"name":"DelayedTaskSchedulerWorker","threadState":{"r13":{"value":0},"rax":{"value":4},"rflags":{"value":535},"cpu":{"value":0},"r14":{"value":0},"rsi":{"value":123145530375936},"r8":{"value":1024},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":123145530375936},"r9":{"value":123145530375808},"r15":{"value":1219772827888},"rbx":{"value":123145530375936},"trap":{"value":133},"err":{"value":33554795},"r11":{"value":535},"rip":{"value":140703500736458},"rbp":{"value":123145530408752},"rsp":{"value":123145530375768},"r12":{"value":0},"rcx":{"value":123145530375768},"flavor":"x86_THREAD_STATE","rdi":{"value":14}},"frames":[{"imageOffset":22474,"symbol":"kevent","symbolLocation":10,"imageIndex":12},{"imageOffset":45174496,"symbol":"uv__io_poll","symbolLocation":1376,"imageIndex":1},{"imageOffset":45097105,"symbol":"uv_run","symbolLocation":481,"imageIndex":1},{"imageOffset":49477104,"symbol":"node::WorkerThreadsTaskRunner::DelayedTaskScheduler::Start()::'lambda'(void*)::__invoke(void*)","symbolLocation":160,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589465,"name":"V8Worker","threadState":{"r13":{"value":1622883457942784},"rax":{"value":4},"rflags":{"value":583},"cpu":{"value":0},"r14":{"value":377856},"rsi":{"value":1622883457942784},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":377856},"r10":{"value":0},"r9":{"value":160},"r15":{"value":0},"rbx":{"value":123145538809856},"trap":{"value":133},"err":{"value":33554737},"r11":{"value":582},"rip":{"value":140703500728078},"rbp":{"value":123145538809584},"rsp":{"value":123145538809432},"r12":{"value":22},"rcx":{"value":123145538809432},"flavor":"x86_THREAD_STATE","rdi":{"value":1219771942392}},"frames":[{"imageOffset":14094,"symbol":"__psynch_cvwait","symbolLocation":10,"imageIndex":12},{"imageOffset":25079,"symbol":"_pthread_cond_wait","symbolLocation":994,"imageIndex":13},{"imageOffset":45153411,"symbol":"uv_cond_wait","symbolLocation":35,"imageIndex":1},{"imageOffset":49477618,"symbol":"node::WorkerThreadsTaskRunner::DelayedTaskScheduler::FlushTasks(uv_async_s*)","symbolLocation":466,"imageIndex":1},{"imageOffset":49466485,"symbol":"node::WorkerThreadsTaskRunner::WorkerThreadsTaskRunner(int, node::PlatformDebugLogLevel)","symbolLocation":1733,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589466,"name":"V8Worker","threadState":{"r13":{"value":1622879162976000},"rax":{"value":260},"rflags":{"value":583},"cpu":{"value":0},"r14":{"value":377856},"rsi":{"value":1622879162976000},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":377856},"r10":{"value":0},"r9":{"value":160},"r15":{"value":0},"rbx":{"value":123145547210752},"trap":{"value":133},"err":{"value":33554737},"r11":{"value":582},"rip":{"value":140703500728078},"rbp":{"value":123145547210480},"rsp":{"value":123145547210328},"r12":{"value":22},"rcx":{"value":123145547210328},"flavor":"x86_THREAD_STATE","rdi":{"value":1219771942392}},"frames":[{"imageOffset":14094,"symbol":"__psynch_cvwait","symbolLocation":10,"imageIndex":12},{"imageOffset":25079,"symbol":"_pthread_cond_wait","symbolLocation":994,"imageIndex":13},{"imageOffset":45153411,"symbol":"uv_cond_wait","symbolLocation":35,"imageIndex":1},{"imageOffset":49477618,"symbol":"node::WorkerThreadsTaskRunner::DelayedTaskScheduler::FlushTasks(uv_async_s*)","symbolLocation":466,"imageIndex":1},{"imageOffset":49466485,"symbol":"node::WorkerThreadsTaskRunner::WorkerThreadsTaskRunner(int, node::PlatformDebugLogLevel)","symbolLocation":1733,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589467,"name":"V8Worker","threadState":{"r13":{"value":1622879162975744},"rax":{"value":4},"rflags":{"value":583},"cpu":{"value":0},"r14":{"value":377856},"rsi":{"value":1622879162975744},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":377856},"r10":{"value":0},"r9":{"value":160},"r15":{"value":0},"rbx":{"value":123145555611648},"trap":{"value":133},"err":{"value":33554737},"r11":{"value":582},"rip":{"value":140703500728078},"rbp":{"value":123145555611376},"rsp":{"value":123145555611224},"r12":{"value":22},"rcx":{"value":123145555611224},"flavor":"x86_THREAD_STATE","rdi":{"value":1219771942392}},"frames":[{"imageOffset":14094,"symbol":"__psynch_cvwait","symbolLocation":10,"imageIndex":12},{"imageOffset":25079,"symbol":"_pthread_cond_wait","symbolLocation":994,"imageIndex":13},{"imageOffset":45153411,"symbol":"uv_cond_wait","symbolLocation":35,"imageIndex":1},{"imageOffset":49477618,"symbol":"node::WorkerThreadsTaskRunner::DelayedTaskScheduler::FlushTasks(uv_async_s*)","symbolLocation":466,"imageIndex":1},{"imageOffset":49466485,"symbol":"node::WorkerThreadsTaskRunner::WorkerThreadsTaskRunner(int, node::PlatformDebugLogLevel)","symbolLocation":1733,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589468,"name":"SignalInspector","threadState":{"r13":{"value":0},"rax":{"value":14},"rflags":{"value":518},"cpu":{"value":0},"r14":{"value":0},"rsi":{"value":123145555656512},"r8":{"value":32210693019496563},"cr2":{"value":0},"rdx":{"value":8},"r10":{"value":8774298243071344750},"r9":{"value":15},"r15":{"value":0},"rbx":{"value":4754127060},"trap":{"value":133},"err":{"value":16777252},"r11":{"value":518},"rip":{"value":140703500716746},"rbp":{"value":123145555656592},"rsp":{"value":123145555656568},"r12":{"value":0},"rcx":{"value":123145555656568},"flavor":"x86_THREAD_STATE","rdi":{"value":62979}},"frames":[{"imageOffset":2762,"symbol":"semaphore_wait_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":45153024,"symbol":"uv_sem_wait","symbolLocation":16,"imageIndex":1},{"imageOffset":51199727,"symbol":"node::inspector::Agent::GetWsUrl() const","symbolLocation":79,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589470,"name":"NetworkConfigWatcher","threadState":{"r13":{"value":21592279046},"rax":{"value":268451845},"rflags":{"value":518},"cpu":{"value":0},"r14":{"value":2},"rsi":{"value":21592279046},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":8589934592},"r10":{"value":263895675568128},"r9":{"value":263895675568128},"r15":{"value":0},"rbx":{"value":123145564053040},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":518},"rip":{"value":140703500716878},"rbp":{"value":123145564052880},"rsp":{"value":123145564052776},"r12":{"value":263895675568128},"rcx":{"value":123145564052776},"flavor":"x86_THREAD_STATE","rdi":{"value":123145564053040}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":508607,"symbol":"__CFRunLoopServiceMachPort","symbolLocation":145,"imageIndex":8},{"imageOffset":503128,"symbol":"__CFRunLoopRun","symbolLocation":1354,"imageIndex":8},{"imageOffset":1321143,"symbol":"_CFRunLoopRunSpecificWithOptions","symbolLocation":496,"imageIndex":8},{"imageOffset":371847,"symbol":"-[NSRunLoop(NSRunLoop) runMode:beforeDate:]","symbolLocation":216,"imageIndex":14},{"imageOffset":29340553,"symbol":"node::PrincipalRealm::get_source_map_error_source() const","symbolLocation":76377,"imageIndex":1},{"imageOffset":29340194,"symbol":"node::PrincipalRealm::get_source_map_error_source() const","symbolLocation":76018,"imageIndex":1},{"imageOffset":16517113,"symbol":"v8::Isolate::Allocate()","symbolLocation":74777,"imageIndex":1},{"imageOffset":19734465,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1777,"imageIndex":1},{"imageOffset":19734248,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1560,"imageIndex":1},{"imageOffset":99163437,"symbol":"ares_dns_rr_get_ttl","symbolLocation":4007037,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589471,"name":"CrShutdownDetector","threadState":{"r13":{"value":0},"rax":{"value":4},"rflags":{"value":583},"cpu":{"value":0},"r14":{"value":123145564085784},"rsi":{"value":123145564085784},"r8":{"value":123145564085697},"cr2":{"value":0},"rdx":{"value":4},"r10":{"value":1},"r9":{"value":18},"r15":{"value":18},"rbx":{"value":1219779159584},"trap":{"value":133},"err":{"value":33554435},"r11":{"value":582},"rip":{"value":140703500719570},"rbp":{"value":123145564086128},"rsp":{"value":123145564085768},"r12":{"value":4},"rcx":{"value":123145564085768},"flavor":"x86_THREAD_STATE","rdi":{"value":18}},"frames":[{"imageOffset":5586,"symbol":"read","symbolLocation":10,"imageIndex":12},{"imageOffset":47470055,"symbol":"node::PrincipalRealm::tick_callback_function() const","symbolLocation":953527,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589472,"name":"NetworkConfigWatcher","threadState":{"r13":{"value":21592279046},"rax":{"value":268451845},"rflags":{"value":518},"cpu":{"value":0},"r14":{"value":2},"rsi":{"value":21592279046},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":8589934592},"r10":{"value":258398117429248},"r9":{"value":258398117429248},"r15":{"value":0},"rbx":{"value":123145572482608},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":518},"rip":{"value":140703500716878},"rbp":{"value":123145572482448},"rsp":{"value":123145572482344},"r12":{"value":258398117429248},"rcx":{"value":123145572482344},"flavor":"x86_THREAD_STATE","rdi":{"value":123145572482608}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":508607,"symbol":"__CFRunLoopServiceMachPort","symbolLocation":145,"imageIndex":8},{"imageOffset":503128,"symbol":"__CFRunLoopRun","symbolLocation":1354,"imageIndex":8},{"imageOffset":1321143,"symbol":"_CFRunLoopRunSpecificWithOptions","symbolLocation":496,"imageIndex":8},{"imageOffset":371847,"symbol":"-[NSRunLoop(NSRunLoop) runMode:beforeDate:]","symbolLocation":216,"imageIndex":14},{"imageOffset":29340553,"symbol":"node::PrincipalRealm::get_source_map_error_source() const","symbolLocation":76377,"imageIndex":1},{"imageOffset":29340194,"symbol":"node::PrincipalRealm::get_source_map_error_source() const","symbolLocation":76018,"imageIndex":1},{"imageOffset":16517113,"symbol":"v8::Isolate::Allocate()","symbolLocation":74777,"imageIndex":1},{"imageOffset":19734465,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1777,"imageIndex":1},{"imageOffset":19734248,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1560,"imageIndex":1},{"imageOffset":99163437,"symbol":"ares_dns_rr_get_ttl","symbolLocation":4007037,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589473,"name":"ThreadPoolForegroundWorker","threadState":{"r13":{"value":17179869186},"rax":{"value":268451845},"rflags":{"value":514},"cpu":{"value":0},"r14":{"value":32},"rsi":{"value":17179869186},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":211119117434880},"r9":{"value":211119117434880},"r15":{"value":0},"rbx":{"value":123145580887280},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":514},"rip":{"value":140703500716878},"rbp":{"value":123145580886608},"rsp":{"value":123145580886504},"r12":{"value":211119117434880},"rcx":{"value":123145580886504},"flavor":"x86_THREAD_STATE","rdi":{"value":123145580887280}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":5475761,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":79121,"imageIndex":1},{"imageOffset":5475226,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78586,"imageIndex":1},{"imageOffset":5475140,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78500,"imageIndex":1},{"imageOffset":1567555,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":63795,"imageIndex":1},{"imageOffset":1565709,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61949,"imageIndex":1},{"imageOffset":1565463,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61703,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589474,"name":"ThreadPoolForegroundWorker","threadState":{"r13":{"value":17179869186},"rax":{"value":268451845},"rflags":{"value":514},"cpu":{"value":0},"r14":{"value":32},"rsi":{"value":17179869186},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":212218629062656},"r9":{"value":212218629062656},"r15":{"value":0},"rbx":{"value":123145589288176},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":514},"rip":{"value":140703500716878},"rbp":{"value":123145589287504},"rsp":{"value":123145589287400},"r12":{"value":212218629062656},"rcx":{"value":123145589287400},"flavor":"x86_THREAD_STATE","rdi":{"value":123145589288176}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":5475761,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":79121,"imageIndex":1},{"imageOffset":5475226,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78586,"imageIndex":1},{"imageOffset":5475140,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78500,"imageIndex":1},{"imageOffset":1567555,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":63795,"imageIndex":1},{"imageOffset":1565709,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61949,"imageIndex":1},{"imageOffset":1565463,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61703,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589475,"name":"ThreadPoolSingleThreadForegroundBlocking0","threadState":{"r13":{"value":17179869186},"rax":{"value":268451845},"rflags":{"value":514},"cpu":{"value":0},"r14":{"value":32},"rsi":{"value":17179869186},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":249602024407040},"r9":{"value":249602024407040},"r15":{"value":0},"rbx":{"value":123145597689072},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":514},"rip":{"value":140703500716878},"rbp":{"value":123145597688400},"rsp":{"value":123145597688296},"r12":{"value":249602024407040},"rcx":{"value":123145597688296},"flavor":"x86_THREAD_STATE","rdi":{"value":123145597689072}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":5475761,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":79121,"imageIndex":1},{"imageOffset":5475226,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78586,"imageIndex":1},{"imageOffset":5475140,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78500,"imageIndex":1},{"imageOffset":1567555,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":63795,"imageIndex":1},{"imageOffset":1565853,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":62093,"imageIndex":1},{"imageOffset":1565534,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61774,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589476,"name":"CompositorTileWorker1","threadState":{"r13":{"value":4294967552},"rax":{"value":260},"rflags":{"value":583},"cpu":{"value":0},"r14":{"value":0},"rsi":{"value":4294967552},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":0},"r9":{"value":161},"r15":{"value":0},"rbx":{"value":123145606090752},"trap":{"value":133},"err":{"value":33554737},"r11":{"value":582},"rip":{"value":140703500728078},"rbp":{"value":123145606090400},"rsp":{"value":123145606090248},"r12":{"value":22},"rcx":{"value":123145606090248},"flavor":"x86_THREAD_STATE","rdi":{"value":1219772381976}},"frames":[{"imageOffset":14094,"symbol":"__psynch_cvwait","symbolLocation":10,"imageIndex":12},{"imageOffset":25079,"symbol":"_pthread_cond_wait","symbolLocation":994,"imageIndex":13},{"imageOffset":9011979,"symbol":"sk_X509_call_free_func","symbolLocation":165339,"imageIndex":1},{"imageOffset":14669390,"symbol":"v8::internal::OptimizingCompileTaskExecutor::RunCompilationJob(v8::internal::OptimizingCompileTaskState&, v8::internal::Isolate*, v8::internal::LocalIsolate&, v8::internal::TurbofanCompilationJob*)","symbolLocation":415854,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589478,"frames":[{"imageOffset":30858,"symbol":"poll","symbolLocation":10,"imageIndex":12},{"imageOffset":47461993,"symbol":"node::PrincipalRealm::tick_callback_function() const","symbolLocation":945465,"imageIndex":1},{"imageOffset":46996603,"symbol":"node::PrincipalRealm::tick_callback_function() const","symbolLocation":480075,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}],"threadState":{"r13":{"value":0},"rax":{"value":4},"rflags":{"value":659},"cpu":{"value":0},"r14":{"value":123145614491504},"rsi":{"value":1},"r8":{"value":1219791368840},"cr2":{"value":0},"rdx":{"value":92},"r10":{"value":92},"r9":{"value":0},"r15":{"value":8},"rbx":{"value":92},"trap":{"value":133},"err":{"value":33554662},"r11":{"value":659},"rip":{"value":140703500744842},"rbp":{"value":123145614491536},"rsp":{"value":123145614491496},"r12":{"value":0},"rcx":{"value":123145614491496},"flavor":"x86_THREAD_STATE","rdi":{"value":123145614491504}}},{"id":589479,"name":"NetworkNotificationThreadMac","threadState":{"r13":{"value":21592279046},"rax":{"value":268451845},"rflags":{"value":518},"cpu":{"value":0},"r14":{"value":2},"rsi":{"value":21592279046},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":8589934592},"r10":{"value":238606908129280},"r9":{"value":238606908129280},"r15":{"value":0},"rbx":{"value":123145622887984},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":518},"rip":{"value":140703500716878},"rbp":{"value":123145622887824},"rsp":{"value":123145622887720},"r12":{"value":238606908129280},"rcx":{"value":123145622887720},"flavor":"x86_THREAD_STATE","rdi":{"value":123145622887984}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":508607,"symbol":"__CFRunLoopServiceMachPort","symbolLocation":145,"imageIndex":8},{"imageOffset":503128,"symbol":"__CFRunLoopRun","symbolLocation":1354,"imageIndex":8},{"imageOffset":1321143,"symbol":"_CFRunLoopRunSpecificWithOptions","symbolLocation":496,"imageIndex":8},{"imageOffset":371847,"symbol":"-[NSRunLoop(NSRunLoop) runMode:beforeDate:]","symbolLocation":216,"imageIndex":14},{"imageOffset":29340553,"symbol":"node::PrincipalRealm::get_source_map_error_source() const","symbolLocation":76377,"imageIndex":1},{"imageOffset":29340194,"symbol":"node::PrincipalRealm::get_source_map_error_source() const","symbolLocation":76018,"imageIndex":1},{"imageOffset":16517113,"symbol":"v8::Isolate::Allocate()","symbolLocation":74777,"imageIndex":1},{"imageOffset":19734465,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1777,"imageIndex":1},{"imageOffset":19734248,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1560,"imageIndex":1},{"imageOffset":99163437,"symbol":"ares_dns_rr_get_ttl","symbolLocation":4007037,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589516,"name":"ThreadPoolSingleThreadSharedBackgroundBlocking1","threadState":{"r13":{"value":17179869186},"rax":{"value":268451845},"rflags":{"value":514},"cpu":{"value":0},"r14":{"value":32},"rsi":{"value":17179869186},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":255116762415104},"r9":{"value":255116762415104},"r15":{"value":0},"rbx":{"value":123145632365808},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":514},"rip":{"value":140703500716878},"rbp":{"value":123145632365136},"rsp":{"value":123145632365032},"r12":{"value":255116762415104},"rcx":{"value":123145632365032},"flavor":"x86_THREAD_STATE","rdi":{"value":123145632365808}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":5475761,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":79121,"imageIndex":1},{"imageOffset":5475226,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78586,"imageIndex":1},{"imageOffset":5475140,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78500,"imageIndex":1},{"imageOffset":1567555,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":63795,"imageIndex":1},{"imageOffset":1569165,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":65405,"imageIndex":1},{"imageOffset":1565549,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61789,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589633,"name":"ThreadPoolForegroundWorker","threadState":{"r13":{"value":17179869186},"rax":{"value":268451845},"rflags":{"value":514},"cpu":{"value":0},"r14":{"value":32},"rsi":{"value":17179869186},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":546521703514112},"r9":{"value":546521703514112},"r15":{"value":0},"rbx":{"value":123145640766704},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":514},"rip":{"value":140703500716878},"rbp":{"value":123145640766032},"rsp":{"value":123145640765928},"r12":{"value":546521703514112},"rcx":{"value":123145640765928},"flavor":"x86_THREAD_STATE","rdi":{"value":123145640766704}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":5475761,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":79121,"imageIndex":1},{"imageOffset":5475226,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78586,"imageIndex":1},{"imageOffset":5475140,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78500,"imageIndex":1},{"imageOffset":1567555,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":63795,"imageIndex":1},{"imageOffset":1565709,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61949,"imageIndex":1},{"imageOffset":1565463,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61703,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589634,"name":"NetworkConfigWatcher","threadState":{"r13":{"value":21592279046},"rax":{"value":268451845},"rflags":{"value":518},"cpu":{"value":0},"r14":{"value":2},"rsi":{"value":21592279046},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":8589934592},"r10":{"value":400235117412352},"r9":{"value":400235117412352},"r15":{"value":0},"rbx":{"value":123145649163824},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":518},"rip":{"value":140703500716878},"rbp":{"value":123145649163664},"rsp":{"value":123145649163560},"r12":{"value":400235117412352},"rcx":{"value":123145649163560},"flavor":"x86_THREAD_STATE","rdi":{"value":123145649163824}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":508607,"symbol":"__CFRunLoopServiceMachPort","symbolLocation":145,"imageIndex":8},{"imageOffset":503128,"symbol":"__CFRunLoopRun","symbolLocation":1354,"imageIndex":8},{"imageOffset":1321143,"symbol":"_CFRunLoopRunSpecificWithOptions","symbolLocation":496,"imageIndex":8},{"imageOffset":371847,"symbol":"-[NSRunLoop(NSRunLoop) runMode:beforeDate:]","symbolLocation":216,"imageIndex":14},{"imageOffset":29340553,"symbol":"node::PrincipalRealm::get_source_map_error_source() const","symbolLocation":76377,"imageIndex":1},{"imageOffset":29340194,"symbol":"node::PrincipalRealm::get_source_map_error_source() const","symbolLocation":76018,"imageIndex":1},{"imageOffset":16517113,"symbol":"v8::Isolate::Allocate()","symbolLocation":74777,"imageIndex":1},{"imageOffset":19734465,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1777,"imageIndex":1},{"imageOffset":19734248,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1560,"imageIndex":1},{"imageOffset":99163437,"symbol":"ares_dns_rr_get_ttl","symbolLocation":4007037,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589635,"name":"ThreadPoolBackgroundWorker","threadState":{"r13":{"value":17179869186},"rax":{"value":268451845},"rflags":{"value":514},"cpu":{"value":0},"r14":{"value":32},"rsi":{"value":17179869186},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":522280908095488},"r9":{"value":522280908095488},"r15":{"value":0},"rbx":{"value":123145657568496},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":514},"rip":{"value":140703500716878},"rbp":{"value":123145657567824},"rsp":{"value":123145657567720},"r12":{"value":522280908095488},"rcx":{"value":123145657567720},"flavor":"x86_THREAD_STATE","rdi":{"value":123145657568496}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":5475761,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":79121,"imageIndex":1},{"imageOffset":5475226,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78586,"imageIndex":1},{"imageOffset":5475140,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78500,"imageIndex":1},{"imageOffset":1567555,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":63795,"imageIndex":1},{"imageOffset":1565757,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61997,"imageIndex":1},{"imageOffset":1565504,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61744,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589636,"name":"ThreadPoolSingleThreadSharedForeground2","threadState":{"r13":{"value":17179869186},"rax":{"value":268451845},"rflags":{"value":514},"cpu":{"value":0},"r14":{"value":32},"rsi":{"value":17179869186},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":520081884839936},"r9":{"value":520081884839936},"r15":{"value":0},"rbx":{"value":123145665969392},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":514},"rip":{"value":140703500716878},"rbp":{"value":123145665968720},"rsp":{"value":123145665968616},"r12":{"value":520081884839936},"rcx":{"value":123145665968616},"flavor":"x86_THREAD_STATE","rdi":{"value":123145665969392}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":5475761,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":79121,"imageIndex":1},{"imageOffset":5475226,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78586,"imageIndex":1},{"imageOffset":5475140,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78500,"imageIndex":1},{"imageOffset":1567555,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":63795,"imageIndex":1},{"imageOffset":1565805,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":62045,"imageIndex":1},{"imageOffset":1565519,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61759,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589637,"name":"ThreadPoolForegroundWorker","threadState":{"r13":{"value":17179869186},"rax":{"value":268451845},"rflags":{"value":514},"cpu":{"value":0},"r14":{"value":32},"rsi":{"value":17179869186},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":403533652295680},"r9":{"value":403533652295680},"r15":{"value":0},"rbx":{"value":123145674370288},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":514},"rip":{"value":140703500716878},"rbp":{"value":123145674369616},"rsp":{"value":123145674369512},"r12":{"value":403533652295680},"rcx":{"value":123145674369512},"flavor":"x86_THREAD_STATE","rdi":{"value":123145674370288}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":5475761,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":79121,"imageIndex":1},{"imageOffset":5475226,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78586,"imageIndex":1},{"imageOffset":5475140,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78500,"imageIndex":1},{"imageOffset":1567555,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":63795,"imageIndex":1},{"imageOffset":1565709,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61949,"imageIndex":1},{"imageOffset":1565463,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61703,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589642,"name":"ThreadPoolSingleThreadSharedForegroundBlocking3","threadState":{"r13":{"value":17179869186},"rax":{"value":268451845},"rflags":{"value":514},"cpu":{"value":0},"r14":{"value":32},"rsi":{"value":17179869186},"r8":{"value":0},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":417827303456768},"r9":{"value":417827303456768},"r15":{"value":0},"rbx":{"value":123145682771184},"trap":{"value":133},"err":{"value":16777263},"r11":{"value":514},"rip":{"value":140703500716878},"rbp":{"value":123145682770512},"rsp":{"value":123145682770408},"r12":{"value":417827303456768},"rcx":{"value":123145682770408},"flavor":"x86_THREAD_STATE","rdi":{"value":123145682771184}},"frames":[{"imageOffset":2894,"symbol":"mach_msg2_trap","symbolLocation":10,"imageIndex":12},{"imageOffset":61257,"symbol":"mach_msg2_internal","symbolLocation":83,"imageIndex":12},{"imageOffset":31588,"symbol":"mach_msg_overwrite","symbolLocation":586,"imageIndex":12},{"imageOffset":3668,"symbol":"mach_msg","symbolLocation":19,"imageIndex":12},{"imageOffset":5475761,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":79121,"imageIndex":1},{"imageOffset":5475226,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78586,"imageIndex":1},{"imageOffset":5475140,"symbol":"node::PrincipalRealm::inspector_disable_async_hooks() const","symbolLocation":78500,"imageIndex":1},{"imageOffset":1567555,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":63795,"imageIndex":1},{"imageOffset":1565805,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":62045,"imageIndex":1},{"imageOffset":1565519,"symbol":"cppgc::internal::PersistentRegionLock::AssertLocked()","symbolLocation":61759,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":589643,"name":"CacheThread_BlockFile","threadState":{"r13":{"value":1219782690784},"rax":{"value":4},"rflags":{"value":583},"cpu":{"value":0},"r14":{"value":1219783504768},"rsi":{"value":0},"r8":{"value":2},"cr2":{"value":0},"rdx":{"value":0},"r10":{"value":1219785044800},"r9":{"value":0},"r15":{"value":1219783505032},"rbx":{"value":123145691172264},"trap":{"value":133},"err":{"value":33554801},"r11":{"value":582},"rip":{"value":140703500756170},"rbp":{"value":123145691172336},"rsp":{"value":123145691172136},"r12":{"value":2147483648},"rcx":{"value":123145691172136},"flavor":"x86_THREAD_STATE","rdi":{"value":42}},"frames":[{"imageOffset":42186,"symbol":"kevent64","symbolLocation":10,"imageIndex":12},{"imageOffset":2590004,"symbol":"node::PrincipalRealm::snapshot_serialize_callback() const","symbolLocation":56548,"imageIndex":1},{"imageOffset":16517113,"symbol":"v8::Isolate::Allocate()","symbolLocation":74777,"imageIndex":1},{"imageOffset":19734465,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1777,"imageIndex":1},{"imageOffset":19734248,"symbol":"v8::Isolate::InstallConditionalFeatures(v8::Local<v8::Context>)","symbolLocation":1560,"imageIndex":1},{"imageOffset":99163437,"symbol":"ares_dns_rr_get_ttl","symbolLocation":4007037,"imageIndex":1},{"imageOffset":10979270,"symbol":"node::PrincipalRealm::enhance_fatal_stack_before_inspector() const","symbolLocation":16166,"imageIndex":1},{"imageOffset":23881,"symbol":"_pthread_start","symbolLocation":115,"imageIndex":13},{"imageOffset":6191,"symbol":"thread_start","symbolLocation":15,"imageIndex":13}]},{"id":593594,"frames":[],"threadState":{"r13":{"value":0},"rax":{"value":33554800},"rflags":{"value":512},"cpu":{"value":0},"r14":{"value":1},"rsi":{"value":106195},"r8":{"value":409604},"cr2":{"value":0},"rdx":{"value":123145461067776},"r10":{"value":0},"r9":{"value":18446744073709551615},"r15":{"value":123145461590904},"rbx":{"value":123145461592064},"trap":{"value":133},"err":{"value":33554800},"r11":{"value":582},"rip":{"value":140703500978188},"rbp":{"value":0},"rsp":{"value":123145461592064},"r12":{"value":5193733},"rcx":{"value":0},"flavor":"x86_THREAD_STATE","rdi":{"value":123145461592064}}},{"id":595090,"frames":[],"threadState":{"r13":{"value":0},"rax":{"value":33554800},"rflags":{"value":512},"cpu":{"value":0},"r14":{"value":1},"rsi":{"value":102267},"r8":{"value":409604},"cr2":{"value":0},"rdx":{"value":123145460531200},"r10":{"value":0},"r9":{"value":18446744073709551615},"r15":{"value":123145461054336},"rbx":{"value":123145461055488},"trap":{"value":133},"err":{"value":33554800},"r11":{"value":582},"rip":{"value":140703500978188},"rbp":{"value":0},"rsp":{"value":123145461055488},"r12":{"value":1982472},"rcx":{"value":0},"flavor":"x86_THREAD_STATE","rdi":{"value":123145461055488}}},{"id":595173,"frames":[],"threadState":{"r13":{"value":0},"rax":{"value":33554800},"rflags":{"value":512},"cpu":{"value":0},"r14":{"value":1},"rsi":{"value":99131},"r8":{"value":409604},"cr2":{"value":0},"rdx":{"value":123145461604352},"r10":{"value":0},"r9":{"value":18446744073709551615},"r15":{"value":123145462127480},"rbx":{"value":123145462128640},"trap":{"value":133},"err":{"value":33554800},"r11":{"value":582},"rip":{"value":140703500978188},"rbp":{"value":0},"rsp":{"value":123145462128640},"r12":{"value":5128198},"rcx":{"value":0},"flavor":"x86_THREAD_STATE","rdi":{"value":123145462128640}}}],
  "usedImages" : [
  {
    "source" : "P",
    "arch" : "x86_64",
    "base" : 4357947392,
    "CFBundleShortVersionString" : "1.12.0",
    "CFBundleIdentifier" : "com.kamp.app",
    "size" : 8192,
    "uuid" : "4c4c4466-5555-3144-a187-be5fe5df241d",
    "path" : "\/Applications\/Kamp.app\/Contents\/MacOS\/Kamp",
    "name" : "Kamp",
    "CFBundleVersion" : "1.12.0"
  },
  {
    "source" : "P",
    "arch" : "x86_64",
    "base" : 4559802368,
    "CFBundleIdentifier" : "com.github.Electron.framework",
    "size" : 186773504,
    "uuid" : "4c4c44dc-5555-3144-a15b-f53dfd847e46",
    "path" : "\/Applications\/Kamp.app\/Contents\/Frameworks\/Electron Framework.framework\/Versions\/A\/Electron Framework",
    "name" : "Electron Framework",
    "CFBundleVersion" : "41.2.1"
  },
  {
    "source" : "P",
    "arch" : "x86_64",
    "base" : 4358139904,
    "CFBundleShortVersionString" : "1.0",
    "CFBundleIdentifier" : "com.github.Squirrel",
    "size" : 90112,
    "uuid" : "4c4c445c-5555-3144-a13f-ab997eb52e18",
    "path" : "\/Applications\/Kamp.app\/Contents\/Frameworks\/Squirrel.framework\/Versions\/A\/Squirrel",
    "name" : "Squirrel",
    "CFBundleVersion" : "1"
  },
  {
    "source" : "P",
    "arch" : "x86_64",
    "base" : 4358664192,
    "CFBundleShortVersionString" : "3.1.0",
    "CFBundleIdentifier" : "com.electron.reactive",
    "size" : 278528,
    "uuid" : "4c4c44b9-5555-3144-a174-05ca8f2d2a08",
    "path" : "\/Applications\/Kamp.app\/Contents\/Frameworks\/ReactiveObjC.framework\/Versions\/A\/ReactiveObjC",
    "name" : "ReactiveObjC",
    "CFBundleVersion" : "0.0.0"
  },
  {
    "source" : "P",
    "arch" : "x86_64",
    "base" : 4358283264,
    "CFBundleShortVersionString" : "1.0",
    "CFBundleIdentifier" : "org.mantle.Mantle",
    "size" : 49152,
    "uuid" : "4c4c44cc-5555-3144-a164-58db4dcd453d",
    "path" : "\/Applications\/Kamp.app\/Contents\/Frameworks\/Mantle.framework\/Versions\/A\/Mantle",
    "name" : "Mantle",
    "CFBundleVersion" : "0.0.0"
  },
  {
    "source" : "P",
    "arch" : "x86_64",
    "base" : 4361490432,
    "size" : 2142208,
    "uuid" : "4c4c448a-5555-3144-a162-14920b52fb22",
    "path" : "\/Applications\/Kamp.app\/Contents\/Frameworks\/Electron Framework.framework\/Versions\/A\/Libraries\/libffmpeg.dylib",
    "name" : "libffmpeg.dylib"
  },
  {
    "source" : "P",
    "arch" : "x86_64h",
    "base" : 4366356480,
    "size" : 53248,
    "uuid" : "e8581c0d-cb70-323f-90a9-0d16881957e4",
    "path" : "\/usr\/lib\/libobjc-trampolines.dylib",
    "name" : "libobjc-trampolines.dylib"
  },
  {
    "size" : 0,
    "source" : "A",
    "base" : 0,
    "uuid" : "00000000-0000-0000-0000-000000000000"
  },
  {
    "source" : "P",
    "arch" : "x86_64h",
    "base" : 140703501254656,
    "CFBundleShortVersionString" : "6.9",
    "CFBundleIdentifier" : "com.apple.CoreFoundation",
    "size" : 4966149,
    "uuid" : "9da51ee2-cd79-3042-a96e-e0b9bd693182",
    "path" : "\/System\/Library\/Frameworks\/CoreFoundation.framework\/Versions\/A\/CoreFoundation",
    "name" : "CoreFoundation",
    "CFBundleVersion" : "4424.1.402"
  },
  {
    "source" : "P",
    "arch" : "x86_64",
    "base" : 140703716261888,
    "CFBundleShortVersionString" : "2.1.1",
    "CFBundleIdentifier" : "com.apple.HIToolbox",
    "size" : 3001167,
    "uuid" : "d03815ad-d064-3912-ae75-d4f7f4748b0a",
    "path" : "\/System\/Library\/Frameworks\/Carbon.framework\/Versions\/A\/Frameworks\/HIToolbox.framework\/Versions\/A\/HIToolbox",
    "name" : "HIToolbox"
  },
  {
    "source" : "P",
    "arch" : "x86_64",
    "base" : 140703566983168,
    "CFBundleShortVersionString" : "6.9",
    "CFBundleIdentifier" : "com.apple.AppKit",
    "size" : 25149655,
    "uuid" : "862ab35d-3dcb-3d7a-bc21-e10207851479",
    "path" : "\/System\/Library\/Frameworks\/AppKit.framework\/Versions\/C\/AppKit",
    "name" : "AppKit",
    "CFBundleVersion" : "2685.50.120"
  },
  {
    "source" : "P",
    "arch" : "x86_64",
    "base" : 140703496896512,
    "size" : 644512,
    "uuid" : "a58aa73b-6617-3a28-ac72-a8a5afd06772",
    "path" : "\/usr\/lib\/dyld",
    "name" : "dyld"
  },
  {
    "source" : "P",
    "arch" : "x86_64",
    "base" : 140703500713984,
    "size" : 255336,
    "uuid" : "fafba22b-d2aa-3fdb-b4e1-451dfe00694e",
    "path" : "\/usr\/lib\/system\/libsystem_kernel.dylib",
    "name" : "libsystem_kernel.dylib"
  },
  {
    "source" : "P",
    "arch" : "x86_64",
    "base" : 140703500972032,
    "size" : 48832,
    "uuid" : "1b15ae36-b1c1-36bd-94cf-3c230733223f",
    "path" : "\/usr\/lib\/system\/libsystem_pthread.dylib",
    "name" : "libsystem_pthread.dylib"
  },
  {
    "source" : "P",
    "arch" : "x86_64",
    "base" : 140703520698368,
    "CFBundleShortVersionString" : "6.9",
    "CFBundleIdentifier" : "com.apple.Foundation",
    "size" : 17013600,
    "uuid" : "af3fb30d-e907-35d3-8108-44ebc9a3e60b",
    "path" : "\/System\/Library\/Frameworks\/Foundation.framework\/Versions\/C\/Foundation",
    "name" : "Foundation",
    "CFBundleVersion" : "4424.1.402"
  }
],
  "sharedCache" : {
  "base" : 140703452479488,
  "size" : 30064771072,
  "uuid" : "ca40fc5e-098c-3173-916e-f3c4af6bec17"
},
  "vmSummary" : "ReadOnly portion of Libraries: Total=1.6G resident=0K(0%) swapped_out_or_unallocated=1.6G(100%)\nWritable regions: Total=1.8G written=0K(0%) resident=0K(0%) swapped_out=0K(0%) unallocated=1.8G(100%)\n\n                                VIRTUAL   REGION \nREGION TYPE                        SIZE    COUNT (non-coalesced) \n===========                     =======  ======= \nAccelerate framework               128K        1 \nActivity Tracing                   256K        1 \nAttributeGraph Data               1024K        1 \nColorSync                            8K        2 \nCoreAnimation                      244K       24 \nCoreGraphics                        12K        2 \nCoreUI image data                  704K        5 \nDispatch continuations           128.0M        1 \nFoundation                          36K        2 \nKernel Alloc Once                  208K        3 \nMALLOC                           160.8M       81 \nMALLOC guard page                   96K       24 \nMach message                        32K        6 \nMemory Tag 253                    48.7G     5111 \nMemory Tag 255                     1.3T      540 \nMemory Tag 255 (reserved)          384K        6         reserved VM address space (unallocated)\nPROTECTED_MEMORY                     4K        1 \nSTACK GUARD                       56.1M       34 \nStack                            226.3M       35 \nVM_ALLOCATE                      415.9M       52 \n__CTF                               824        1 \n__DATA                            46.3M     1007 \n__DATA_CONST                     130.0M     1056 \n__DATA_DIRTY                      8165K      886 \n__FONT_DATA                        2352        1 \n__LINKEDIT                       157.0M        9 \n__OBJC_RO                         65.1M        1 \n__OBJC_RW                         2596K        3 \n__TEXT                             1.4G     1074 \n__TPRO_CONST                         16        2 \nmapped file                      286.0M       64 \nshared memory                     1320K       20 \n===========                     =======  ======= \nTOTAL                              1.4T    10056 \nTOTAL, minus reserved VM space     1.4T    10056 \n",
  "legacyInfo" : {
  "threadTriggered" : {
    "name" : "CrBrowserMain",
    "queue" : "com.apple.main-thread"
  }
},
  "logWritingSignature" : "9c92bb4f0e28217a1452bfd6c1415c907baae905",
  "roots_installed" : 0,
  "bug_type" : "309",
  "trmStatus" : 2105856,
  "trialInfo" : {
  "rollouts" : [
    {
      "rolloutId" : "67181b10c68c361a728c7cfa",
      "factorPackIds" : [

      ],
      "deploymentId" : 240000005
    },
    {
      "rolloutId" : "64628732bf2f5257dedc8988",
      "factorPackIds" : [

      ],
      "deploymentId" : 240000001
    }
  ],
  "experiments" : [

  ]
}
}

Model: MacBookPro16,1, BootROM 2103.100.6.0.0 (iBridge: 23.16.14242.0.0,0), 8 processors, 8-Core Intel Core i9, 2.4 GHz, 64 GB, SMC 
Graphics: Intel UHD Graphics 630, Intel UHD Graphics 630, Built-In
Graphics: AMD Radeon Pro 5500M, AMD Radeon Pro 5500M, PCIe, 4 GB
Display: Color LCD, 3072 x 1920 Retina, Main, MirrorOff, Online
Memory Module: BANK 0/ChannelA-DIMM0, 32 GB, DDR4, 2667 MHz, Micron, MT40A4G8BAF-062E:B
Memory Module: BANK 2/ChannelB-DIMM0, 32 GB, DDR4, 2667 MHz, Micron, MT40A4G8BAF-062E:B
AirPort: spairport_wireless_card_type_wifi (0x14E4, 0x7BF), wl0: Jul 26 2024 22:36:01 version 9.30.514.0.32.5.94 FWID 01-68d7ff80
AirPort: 
Bluetooth: Version (null), 0 services, 0 devices, 0 incoming serial ports
Network Service: Wi-Fi, AirPort, en0
Thunderbolt Bus: MacBook Pro, Apple Inc., 63.5
Thunderbolt Bus: MacBook Pro, Apple Inc., 63.5
<!-- SECTION:DESCRIPTION:END -->
