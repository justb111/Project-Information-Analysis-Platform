// 模拟登录（替换为真实接口）
function login(username, password) {
    return new Promise((resolve, reject) => {
        setTimeout(() => {
            if (username === 'admin' && password === '123456') {
                resolve({ success: true });
            } else {
                reject(new Error('用户名或密码错误'));
            }
        }, 500);
    });
}

// 模拟漏测数据（替换为真实API）
async function fetchLeakData() {
    // 模拟返回的数据包含 project, taskName, testId, executor, leakType, leakAnalysis, version, testSteps, reason, operationDate, isNonCompliance
    return [];
}

// 模拟风险数据（替换为真实API）
async function fetchRiskData() {
    return [];
}

// 辅助：按项目分组
function groupByProject(data) {
    const groups = {};
    data.forEach(item => {
        const proj = item.project;
        if (!groups[proj]) groups[proj] = [];
        groups[proj].push(item);
    });
    return groups;
}

// 渲染漏测分组卡片
function renderLeakGroups(data) {
    const container = document.getElementById('leakGroups');
    if (!data || data.length === 0) {
        container.innerHTML = '<div class="empty-message">暂无漏测分析数据</div>';
        return;
    }
    const groups = groupByProject(data);
    container.innerHTML = '';
    for (const [project, items] of Object.entries(groups)) {
        const leakCount = items.filter(item => !item.isNonCompliance).length;
        const nonComplianceCount = items.filter(item => item.isNonCompliance).length;
        
        const card = document.createElement('div');
        card.className = 'project-group';
        card.innerHTML = `
            <h3>📁 项目：${escapeHtml(project)}</h3>
            <table class="data-table">
                <thead>
                    <tr><th>项目</th><th>执行任务</th><th>执行漏测条数</th><th>不合规条数</th><th>漏测分析</th><th>不合规分析</th><th>漏测/不合规详情</th></tr>
                </thead>
                <tbody>
                    ${items.map(item => `
                        <tr>
                            <td>${escapeHtml(item.project)}</td>
                            <td>${escapeHtml(item.taskName)}</td>
                            <td>${leakCount}</td>
                            <td>${nonComplianceCount}</td>
                            <td>${escapeHtml(item.leakAnalysis)}</td>
                            <td>${item.isNonCompliance ? escapeHtml(item.reason) : '-'}</td>
                            <td><button class="leak-detail-btn" data-project="${escapeHtml(item.project)}" data-testid="${escapeHtml(item.testId)}">查看详情</button></td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        `;
        container.appendChild(card);
    }
    
    // 添加详情按钮事件监听
    document.querySelectorAll('.leak-detail-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const project = this.getAttribute('data-project');
            const testId = this.getAttribute('data-testid');
            showLeakDetailPage(project, testId);
        });
    });
}

// 渲染风险分组卡片
function renderRiskGroups(data) {
    const container = document.getElementById('riskProjectList');
    if (!container) {
        console.error('riskProjectList元素不存在');
        return;
    }
    if (!data || data.length === 0) {
        container.innerHTML = '<div class="empty-message">暂无风险分析数据</div>';
        return;
    }
    const groups = groupByProject(data);
    container.innerHTML = '';
    for (const [project, items] of Object.entries(groups)) {
        const card = document.createElement('div');
        card.className = 'project-list-item';
        card.style.cssText = `
            padding: 15px;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            margin-bottom: 10px;
            cursor: pointer;
            transition: all 0.2s ease;
            background-color: #f8fafc;
        `;
        
        card.onmouseover = function() {
            this.style.backgroundColor = '#e2e8f0';
        };
        card.onmouseout = function() {
            this.style.backgroundColor = '#f8fafc';
        };
        
        let cardContent = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                <h4 style="margin: 0; font-size: 16px;">${escapeHtml(project)}</h4>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; font-size: 14px;">
                <div>
                    <strong>BUG风险ID：</strong>
                    <div style="margin-top: 5px;">
        `;
        
        // 添加Bug ID列表
        if (items.length > 0) {
            items.forEach(item => {
                if (item.bugId) {
                    cardContent += `<span style="display: inline-block; background: #e0f2fe; color: #0284c7; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-right: 5px; margin-bottom: 5px;">${escapeHtml(item.bugId)}</span>`;
                }
            });
        } else {
            cardContent += '<span style="color: #94a3b8;">无</span>';
        }
        
        cardContent += `
                    </div>
                </div>
                <div>
                    <div style="margin-bottom: 10px;">
                        <strong>BUG风险数量：</strong>
                        <span>${items.length}</span>
                    </div>
                    <div>
                        <strong>高风险数量：</strong>
                        <span>${items.filter(item => item.riskLevel === 'high').length}</span>
                    </div>
                </div>
            </div>
        `;
        
        card.innerHTML = cardContent;
        container.appendChild(card);
        
        // 添加点击事件
        card.addEventListener('click', function() {
            showDetailPage(project);
        });
    }
    console.log('风险数据渲染完成');
}

// 首页概览
function renderProjectSummary(leakData, riskData) {
    const container = document.getElementById('projectSummary');
    const projects = new Set();
    if (leakData) leakData.forEach(i => projects.add(i.project));
    if (riskData) riskData.forEach(i => projects.add(i.project));
    if (projects.size === 0) {
        container.innerHTML = '<p>暂无项目数据</p>';
        return;
    }
    container.innerHTML = `<ul style="list-style: none; padding: 0;">${Array.from(projects).map(p => `<li>• ${escapeHtml(p)}</li>`).join('')}</ul>`;
}

// 加载所有数据
let leakDataCache = null;
let riskDataCache = null;
async function loadAllData() {
    try {
        const [leak, risk] = await Promise.all([fetchLeakData(), fetchRiskData()]);
        leakDataCache = leak;
        riskDataCache = risk;
        console.log('加载的数据:', { leak, risk });
        renderLeakGroups(leakDataCache);
        renderRiskGroups(riskDataCache);
        renderProjectSummary(leakDataCache, riskDataCache);
        console.log('数据加载完成');
    } catch (err) {
        console.error('数据加载失败:', err);
        document.getElementById('leakGroups').innerHTML = '<div class="empty-message">数据加载失败</div>';
        const riskGroups = document.getElementById('riskGroups');
        if (riskGroups) {
            riskGroups.innerHTML = '<div class="empty-message">数据加载失败</div>';
        } else {
            console.error('riskGroups元素不存在');
        }
    }
}

// 详情页面显示
function showDetailPage(project) {
    const detailPanel = document.getElementById('detailPanel');
    const riskPanel = document.getElementById('riskPanel');
    const homePanel = document.getElementById('homePanel');
    const leakPanel = document.getElementById('leakPanel');
    
    // 隐藏所有面板，显示详情面板
    [homePanel, leakPanel, riskPanel, detailPanel].forEach(panel => panel.classList.remove('active'));
    detailPanel.classList.add('active');
    
    // 获取该项目的风险数据
    const projectData = riskDataCache.filter(item => item.project === project);
    const detailContent = document.getElementById('detailContent');
    
    if (projectData.length === 0) {
        detailContent.innerHTML = '<div class="empty-message">暂无该项目的详细数据</div>';
        return;
    }
    
    // 生成详情内容
    detailContent.innerHTML = `
        <div class="detail-card">
            <h3>项目：${escapeHtml(project)}</h3>
            <p><strong>总Bug数量：</strong>${projectData.length}</p>
        </div>
        ${projectData.map(item => {
            let riskClass = '';
            if (item.riskLevel === 'high') riskClass = 'high-risk';
            else if (item.riskLevel === 'medium') riskClass = 'medium-risk';
            else riskClass = 'low-risk';
            
            return `
                <div class="detail-card ${riskClass}">
                    <h3>BugID：${escapeHtml(item.bugId)}</h3>
                    <p><strong>Bug描述：</strong>${escapeHtml(item.bugDescription)}</p>
                    <p><strong>风险分析：</strong>${escapeHtml(item.riskAnalysis)}</p>
                    <p><strong>是否卡版本：</strong>${escapeHtml(item.isBlockVersion)}</p>
                    <p><strong>是否交付测试部Bug：</strong>${item.isDeliveryTest ? '是' : '否'}</p>
                    <p><strong>Tags：</strong>${item.tags ? item.tags.map(tag => `<span style="display: inline-block; background: #e0f2fe; color: #0284c7; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-right: 5px; margin-bottom: 5px;">${escapeHtml(tag)}</span>`).join('') : '无'}</p>
                </div>
            `;
        }).join('')}
    `;
}

// 触发AI分析（仅在用户主动请求时调用）
function triggerAIAnalysis(project, data) {
    // 移除自动触发的AI分析，只在用户主动提问时回复
}

// 漏测详情页面显示
function showLeakDetailPage(project, testId) {
    const leakDetailPanel = document.getElementById('leakDetailPanel');
    const leakPanel = document.getElementById('leakPanel');
    const homePanel = document.getElementById('homePanel');
    const riskPanel = document.getElementById('riskPanel');
    const detailPanel = document.getElementById('detailPanel');
    
    // 隐藏所有面板，显示漏测详情面板
    [homePanel, leakPanel, riskPanel, detailPanel, leakDetailPanel].forEach(panel => panel.classList.remove('active'));
    leakDetailPanel.classList.add('active');
    
    // 获取该测试用例的详细数据
    const testData = leakDataCache.find(item => item.project === project && item.testId === testId);
    const leakDetailContent = document.getElementById('leakDetailContent');
    
    if (!testData) {
        leakDetailContent.innerHTML = '<div class="empty-message">暂无该测试用例的详细数据</div>';
        return;
    }
    
    // 生成详情内容
    leakDetailContent.innerHTML = `
        <div class="detail-card">
            <h3>项目：${escapeHtml(project)}</h3>
            <h4>测试用例：${escapeHtml(testData.taskName)}</h4>
            <p><strong>TestID：</strong>${escapeHtml(testData.testId)}</p>
            <p><strong>测试版本号：</strong>${escapeHtml(testData.version)}</p>
            <p><strong>操作日期：</strong>${escapeHtml(testData.operationDate)}</p>
            <p><strong>测试步骤：</strong></p>
            <pre>${escapeHtml(testData.testSteps)}</pre>
            ${testData.isNonCompliance ? 
                `<p><strong>不合规原因：</strong>${escapeHtml(testData.reason)}</p>` : 
                `<p><strong>漏测原因：</strong>${escapeHtml(testData.reason)}</p>`
            }
        </div>
    `;
}

// 导航切换
function setupNavigation() {
    const homeBtn = document.querySelector('[data-tab="home"]');
    const leakBtn = document.querySelector('[data-tab="leak"]');
    const riskBtn = document.querySelector('[data-tab="risk"]');
    const projectBtn = document.querySelector('[data-tab="project"]');
    const homePanel = document.getElementById('homePanel');
    const leakPanel = document.getElementById('leakPanel');
    const riskPanel = document.getElementById('riskPanel');
    const projectPanel = document.getElementById('projectPanel');
    const detailPanel = document.getElementById('detailPanel');
    const leakDetailPanel = document.getElementById('leakDetailPanel');

    function setActive(tab) {
        [homeBtn, leakBtn, riskBtn, projectBtn].forEach(btn => btn.classList.remove('active'));
        if (tab === 'home') homeBtn.classList.add('active');
        else if (tab === 'leak') leakBtn.classList.add('active');
        else if (tab === 'risk') riskBtn.classList.add('active');
        else if (tab === 'project') projectBtn.classList.add('active');

        [homePanel, leakPanel, riskPanel, projectPanel, detailPanel, leakDetailPanel].forEach(panel => panel.classList.remove('active'));
        if (tab === 'home') homePanel.classList.add('active');
        else if (tab === 'leak') leakPanel.classList.add('active');
        else if (tab === 'risk') riskPanel.classList.add('active');
        else if (tab === 'project') projectPanel.classList.add('active');
    }

    homeBtn.addEventListener('click', () => setActive('home'));
    leakBtn.addEventListener('click', () => setActive('leak'));
    riskBtn.addEventListener('click', () => setActive('risk'));
    projectBtn.addEventListener('click', () => setActive('project'));
    
    // 返回按钮事件
    document.getElementById('backBtn').addEventListener('click', () => setActive('risk'));
    document.getElementById('leakBackBtn').addEventListener('click', () => setActive('leak'));
    
    // 初始激活风险分析板块
    setActive('risk');
}

// 初始化搜索功能
function initSearch() {
    // 风险分析搜索
    const searchBtn = document.getElementById('searchBtn');
    const searchInput = document.getElementById('projectSearch');
    
    function performRiskSearch() {
        const keyword = searchInput.value.trim().toLowerCase();
        if (!keyword) {
            renderRiskGroups(riskDataCache);
            return;
        }
        
        const filteredData = riskDataCache.filter(item => 
            item.project.toLowerCase().includes(keyword)
        );
        renderRiskGroups(filteredData);
    }
    
    searchBtn.addEventListener('click', performRiskSearch);
    searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            performRiskSearch();
        }
    });
    
    // 漏测分析搜索
    const leakSearchBtn = document.getElementById('leakSearchBtn');
    const leakSearchInput = document.getElementById('leakSearch');
    
    function performLeakSearch() {
        const keyword = leakSearchInput.value.trim().toLowerCase();
        if (!keyword) {
            renderLeakGroups(leakDataCache);
            return;
        }
        
        const filteredData = leakDataCache.filter(item => 
            item.project.toLowerCase().includes(keyword)
        );
        renderLeakGroups(filteredData);
    }
    
    leakSearchBtn.addEventListener('click', performLeakSearch);
    leakSearchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            performLeakSearch();
        }
    });
}

// 初始化AI聊天功能
function initAIChat() {
    const sendBtn = document.getElementById('sendBtn');
    const aiInput = document.getElementById('aiInput');
    const chatMessages = document.getElementById('chatMessages');
    
    // 会话管理
    let conversationId = 'default';
    let currentProjectKey = null;
    let chatHistory = [];
    let currentEventSource = null;
    
    function sendMessage() {
        const message = aiInput.value.trim();
        if (!message) return;
        
        // 添加用户消息到聊天历史
        chatHistory.push({ role: 'user', content: message });
        
        // 添加用户消息到界面
        chatMessages.innerHTML += `
            <div class="message user animate-fade-in">
                <div class="message-content">
                    <p>${escapeHtml(message)}</p>
                </div>
                <div class="avatar user-avatar">👤</div>
            </div>
        `;
        aiInput.value = '';
        chatMessages.scrollTop = chatMessages.scrollHeight;
        
        // 显示加载中消息（包含思考过程）
        const loadingMessageId = 'loading-' + Date.now();
        chatMessages.innerHTML += `
            <div class="message ai" id="${loadingMessageId}">
                <div class="avatar ai-avatar">🤖</div>
                <div class="message-content">
                    <div class="thinking-section">
                        <button class="toggle-thinking" disabled>🔍 正在思考...</button>
                        <div class="thinking-content" style="display: block;">
                            <p>正在分析您的问题，从Jira获取数据，进行风险评估...</p>
                        </div>
                    </div>
                </div>
            </div>
        `;
        chatMessages.scrollTop = chatMessages.scrollHeight;
        
        // 调用后端API，发送用户查询
        console.log('开始调用API...');
        console.log('用户输入:', message);
        console.log('会话ID:', conversationId);
        console.log('当前项目键:', currentProjectKey);
        
        // 检测项目键 - 支持更多格式，包括CN6这样的形式
        const projectKeyMatch = message.match(/[A-Za-z]+\d+|X?\d{4}(?:-[a-zA-Z0-9]+)?/);
        const hasProjectKey = projectKeyMatch !== null;
        
        let projectKey = currentProjectKey;
        if (hasProjectKey) {
            projectKey = projectKeyMatch[0];
            currentProjectKey = projectKey; // 更新当前项目键
        }
        
        if (!projectKey) {
            // 没有项目键，提供通用回复
            setTimeout(() => {
                // 移除加载消息
                const loadingMessage = document.getElementById(loadingMessageId);
                if (loadingMessage) {
                    loadingMessage.remove();
                }
                
                const analysis = `我理解您的问题："${message}"。\n\n作为AI分析助手，我可以帮助您分析项目风险、Bug情况等。请提供具体的项目键（如X6840、X6878等），或者告诉我您需要分析的具体内容，我会为您提供专业的分析和建议。`;
                
                // 添加AI回复到聊天历史
                chatHistory.push({ role: 'assistant', content: analysis });
                
                let responseHtml = `
                    <div class="message ai">
                        <div class="avatar ai-avatar">🤖</div>
                        <div class="message-content">
                            <div class="answer-section">
                                ${formatResponse(analysis)}
                            </div>
                        </div>
                    </div>
                `;
                
                chatMessages.innerHTML += responseHtml;
                chatMessages.scrollTop = chatMessages.scrollHeight;
            }, 500);
            return;
        }
        
        // 有项目键，调用后端API获取真实数据
        console.log('使用项目键:', projectKey);
        
        // 检测是否是详细报告请求
        const isDetailedReport = message.includes('详细报告') || message.includes('详细风险报告');
        if (isDetailedReport) {
            console.log('用户请求详细报告');
        }
        
        // 移除加载消息
        const loadingMessage = document.getElementById(loadingMessageId);
        if (loadingMessage) {
            loadingMessage.remove();
        }
        
        // 调用后端API，使用SSE流式响应
        const aiMessageId = 'ai-message-' + Date.now();
        const thinkingSectionId = 'thinking-section-' + Date.now();
        
        // 创建统一的AI消息容器，包含头像、思考过程和结果
        let aiMessageHtml = `
            <div class="message ai" id="${aiMessageId}">
                <div class="avatar ai-avatar">🤖</div>
                <div class="message-content">
                    <!-- 思考过程区域 -->
                    <div class="thinking-section" id="${thinkingSectionId}">
                        <div class="thinking-header" style="display: flex; align-items: center; cursor: pointer; margin-bottom: 8px;">
                            <span class="thinking-tag" style="background-color: #e0f2fe; color: #0284c7; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 500; cursor: pointer;">思考过程</span>
                            <span class="thinking-arrow" style="margin-left: 8px; transition: transform 0.3s ease; font-size: 10px;">▼</span>
                        </div>
                        <div class="thinking-content" style="display: block;">
                            <p></p>
                        </div>
                    </div>
                    <!-- 结果区域 -->
                    <div class="answer-section">
                        <span class="answer-text"></span>
                        <span class="cursor"></span>
                    </div>
                </div>
            </div>
        `;
        
        chatMessages.innerHTML += aiMessageHtml;
        chatMessages.scrollTop = chatMessages.scrollHeight;
        
        const aiMessage = document.getElementById(aiMessageId);
        const thinkingSection = document.getElementById(thinkingSectionId);
        const thinkingContent = thinkingSection.querySelector('.thinking-content p');
        const thinkingContentDiv = thinkingSection.querySelector('.thinking-content');
        const thinkingHeader = thinkingSection.querySelector('.thinking-header');
        const thinkingTag = thinkingSection.querySelector('.thinking-tag');
        const thinkingArrow = thinkingSection.querySelector('.thinking-arrow');
        const messageContent = aiMessage.querySelector('.message-content');
        const answerText = aiMessage.querySelector('.answer-text');
        const cursor = aiMessage.querySelector('.cursor');
        
        let isThinking = true; // 标记是否还在思考过程中
        
        // 构建SSE URL - 使用相对路径
        const sseUrl = new URL('/api/analyze', window.location.origin);
        sseUrl.searchParams.append('project_key', projectKey);
        sseUrl.searchParams.append('user_query', message);
        sseUrl.searchParams.append('conversation_id', conversationId);
        sseUrl.searchParams.append('chat_history', JSON.stringify(chatHistory));
        if (isDetailedReport) {
            sseUrl.searchParams.append('detailed_report', 'true');
        }
        
        // 创建SSE连接
        console.log('发送请求到:', sseUrl.toString());
        const eventSource = new EventSource(sseUrl);
        currentEventSource = eventSource;
        
        // 切换到停止按钮
        sendBtn.textContent = '停止';
        sendBtn.classList.add('stop');
        sendBtn.onclick = function() {
            if (currentEventSource) {
                currentEventSource.close();
                currentEventSource = null;
                cursor.style.display = 'none';
                sendBtn.textContent = '发送';
                sendBtn.classList.remove('stop');
                sendBtn.onclick = sendMessage;
                // 显示停止消息
                answerText.textContent += '\n\n（分析已手动停止）';
            }
        };
        
        // 处理SSE事件
        eventSource.onmessage = function(event) {
            console.log('收到SSE事件:', event.data);
            
            if (event.data === '[DONE]') {
                // 结束标记
                console.log('收到结束标记');
                cursor.style.display = 'none';
                eventSource.close();
                // 恢复发送按钮
                sendBtn.textContent = '发送';
                sendBtn.classList.remove('stop');
                sendBtn.onclick = sendMessage;
                currentEventSource = null;
                return;
            }
            
            try {
                const data = JSON.parse(event.data);
                console.log('解析SSE数据:', data);
                
                if (data.type === 'thinking') {
                    // 处理思考过程 - 思考过程中自动展开显示
                    console.log('收到思考过程数据:', data.content);
                    thinkingContent.textContent += data.content;
                    chatMessages.scrollTop = chatMessages.scrollHeight;
                } else if (data.type === 'answer') {
                    // 第一次收到答案时，显示答案部分并折叠思考过程
                    if (isThinking) {
                        isThinking = false;
                        // 显示答案部分
                        console.log('显示答案部分');
                        messageContent.style.display = 'block';
                        // 折叠思考过程
                        console.log('折叠思考过程');
                        thinkingContentDiv.style.display = 'none';
                        thinkingArrow.style.transform = 'rotate(0deg)';
                        // 清空之前的答案内容
                        answerText.textContent = '';
                    }
                    // 处理最终答案（逐字输出）
                    console.log('收到答案数据:', data.content);
                    answerText.textContent += data.content;
                    chatMessages.scrollTop = chatMessages.scrollHeight;
                } else if (data.type === 'data') {
                    // 处理数据更新
                    updatePanelByType(data.content);
                } else if (data.type === 'error') {
                    // 处理错误
                    answerText.textContent = '抱歉，暂时无法分析数据：' + data.content;
                    cursor.style.display = 'none';
                    eventSource.close();
                    // 恢复发送按钮
                    sendBtn.textContent = '发送';
                    sendBtn.classList.remove('stop');
                    sendBtn.onclick = sendMessage;
                    currentEventSource = null;
                }
            } catch (error) {
                console.error('解析SSE数据失败:', error);
            }
        };
        
        eventSource.onerror = function(error) {
            console.error('SSE连接错误:', error);
            // 检查是否是正常关闭（某些浏览器会在连接关闭时触发onerror）
            if (eventSource.readyState === EventSource.CLOSED) {
                console.log('SSE连接正常关闭');
                // 恢复发送按钮
                sendBtn.textContent = '发送';
                sendBtn.classList.remove('stop');
                sendBtn.onclick = sendMessage;
                currentEventSource = null;
                return;
            }
            cursor.style.display = 'none';
            eventSource.close();
            
            // 显示错误消息
            answerText.textContent = '抱歉，暂时无法分析数据，请稍后再试。';
            
            // 恢复发送按钮
            sendBtn.textContent = '发送';
            sendBtn.classList.remove('stop');
            sendBtn.onclick = sendMessage;
            currentEventSource = null;
        };
        
        // 绑定思考过程切换事件
        if (thinkingHeader) {
            thinkingHeader.addEventListener('click', function() {
                if (thinkingContentDiv.style.display === 'none') {
                    thinkingContentDiv.style.display = 'block';
                    thinkingArrow.style.transform = 'rotate(180deg)';
                } else {
                    thinkingContentDiv.style.display = 'none';
                    thinkingArrow.style.transform = 'rotate(0deg)';
                }
            });
        }
    }
    
    sendBtn.addEventListener('click', sendMessage);
    aiInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            sendMessage();
        }
    });
    
    // 确保发送按钮和输入框能正常工作
    console.log('AI聊天初始化完成');
    console.log('sendBtn:', sendBtn);
    console.log('aiInput:', aiInput);
    console.log('chatMessages:', chatMessages);
    
    // 初始AI欢迎消息
    chatMessages.innerHTML = `
        <div class="message ai">
            <div class="avatar ai-avatar">🤖</div>
            <div class="message-content">
                <p>欢迎使用AI分析助手！我可以帮助您分析项目风险、Bug情况等。请输入您的问题，我会为您提供专业的分析建议。</p>
            </div>
        </div>
    `;
    
    // 绑定思考过程切换按钮
    chatMessages.addEventListener('click', function(e) {
        if (e.target.classList.contains('toggle-thinking')) {
            const thinkingContent = e.target.nextElementSibling;
            if (thinkingContent.style.display === 'none') {
                thinkingContent.style.display = 'block';
                e.target.textContent = '🔍 隐藏思考过程';
            } else {
                thinkingContent.style.display = 'none';
                e.target.textContent = '🔍 显示思考过程';
            }
        }
    });
}

// 格式化AI回复内容，使其结构化显示
function formatResponse(text) {
    if (!text) return '<p>无内容</p>';
    
    // 处理换行和段落
    let formatted = text.replace(/\n\n/g, '</p><p>');
    formatted = formatted.replace(/\n/g, '<br>');
    
    // 处理列表
    formatted = formatted.replace(/\- (.*?)(?=\n|$)/g, '<li>$1</li>');
    formatted = formatted.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>');
    
    // 处理粗体
    formatted = formatted.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    
    // 处理BUG ID列表
    formatted = formatted.replace(/(X\d+\-\d+)(,? )?/g, '<span class="bug-id">$1</span>$2');
    
    return `<p>${formatted}</p>`;
}

// 根据panel_type更新对应的左侧面板
function updatePanelByType(data) {
    const panelType = data.panel_type || 'risk';
    
    // 根据类型切换到对应的面板
    if (panelType === 'risk') {
        // 切换到风险分析面板
        document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
        document.querySelectorAll('.panel').forEach(panel => panel.classList.remove('active'));
        
        // 检查元素是否存在
        const riskNavBtn = document.querySelector('[data-tab="risk"]');
        const riskPanel = document.getElementById('riskPanel');
        if (riskNavBtn) {
            riskNavBtn.classList.add('active');
        }
        if (riskPanel) {
            riskPanel.classList.add('active');
        }
        
        // 更新风险分析面板内容
        updateRiskPanel(data);
    } else if (panelType === 'leak') {
        // 切换到漏测分析面板
        document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
        document.querySelectorAll('.panel').forEach(panel => panel.classList.remove('active'));
        
        // 检查元素是否存在
        const leakNavBtn = document.querySelector('[data-tab="leak"]');
        const leakPanel = document.getElementById('leakPanel');
        if (leakNavBtn) {
            leakNavBtn.classList.add('active');
        }
        if (leakPanel) {
            leakPanel.classList.add('active');
        }
        
        // 更新漏测分析面板内容
        updateLeakPanel(data);
    }
}

// 更新风险分析面板
function updateRiskPanel(data) {
    const projectListContainer = document.getElementById('riskProjectList');
    const analysisSummaryContainer = document.getElementById('riskAnalysisSummary');
    const commonIssuesContainer = document.getElementById('commonIssuesContainer');
    if (!projectListContainer || !analysisSummaryContainer || !commonIssuesContainer) return;
    
    // 获取项目键
    const projectKey = data.project_key || '未知项目';
    
    // 查找是否已存在相同项目的元素
    let existingProjectItem = null;
    const existingItems = projectListContainer.querySelectorAll('.project-list-item');
    existingItems.forEach(item => {
        const itemProjectKey = item.getAttribute('data-project-key');
        if (itemProjectKey === projectKey) {
            existingProjectItem = item;
        }
    });
    
    // 分离本项目问题和共性问题
    const projectIssues = [];
    const commonIssues = [];
    
    if (data.issues && data.issues.length > 0) {
        data.issues.forEach(issue => {
            // 检查问题是否属于本项目
            const issueProject = issue.project || issue.fields?.project?.key || '';
            const currentProject = data.project_key || '';
            const issueKey = issue.bug_key || issue.key || '';
            
            // 只有当问题的key或project包含当前项目关键字时，才认为是本项目的问题
            if (currentProject && (issueKey.includes(currentProject) || (issueProject && issueProject.includes(currentProject)))) {
                projectIssues.push(issue);
            } else {
                commonIssues.push(issue);
            }
        });
    }
    
    // 计算风险相关统计数据
    const mpBlockCount = countMPBlockIssues(projectIssues);
    const blockingCount = countBlockingIssues(projectIssues);
    const deliveryTestCount = countDeliveryTestIssues(projectIssues);
    
    // 计算交付测试部bug数量和ID列表
    const deliveryTestBugs = projectIssues.filter(issue => {
        const summary = issue.summary || '';
        return summary.includes('交付');
    });
    const deliveryTestBugCount = deliveryTestBugs.length;
    
    // 计算阻塞问题数量和ID列表
    const blockingIssues = projectIssues.filter(issue => {
        const tags = issue.tags || issue.labels || [];
        return tags.some(tag => tag.includes('阻塞') || tag.includes('MP block') || tag.includes('must resolve'));
    });
    const blockingIssueCount = blockingIssues.length;
    
    // 按状态分类问题
    const issuesByStatus = {};
    projectIssues.forEach(issue => {
        const status = issue.status || issue.fields?.status?.name || '未知';
        if (!issuesByStatus[status]) {
            issuesByStatus[status] = [];
        }
        issuesByStatus[status].push(issue);
    });
    
    // 提取所有tag
    const allTags = [];
    projectIssues.forEach(issue => {
        if (issue.tags) {
            issue.tags.forEach(tag => {
                if (!allTags.includes(tag)) {
                    allTags.push(tag);
                }
            });
        }
    });
    
    // 构建项目内容HTML
    let projectItemContent = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">
            <h4 style="margin: 0; font-size: 16px;">${escapeHtml(projectKey)}</h4>
            <span style="background: #e0f2fe; color: #0284c7; padding: 2px 8px; border-radius: 12px; font-size: 12px;">${projectIssues.length} 个问题</span>
        </div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; font-size: 14px;">
            <div>
                <strong>BUG风险ID：</strong>
                <div style="margin-top: 3px;">
    `
    
    // 添加Bug ID列表
    if (projectIssues.length > 0) {
        projectIssues.forEach((issue, index) => {
            const bugKey = issue.bug_key || issue.key || '';
            if (bugKey) {
                projectItemContent += `<span style="display: inline-block; background: #e0f2fe; color: #0284c7; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-right: 5px; margin-bottom: 5px;">${escapeHtml(bugKey)}</span>`;
            }
        });
    } else {
        projectItemContent += '<span style="color: #94a3b8;">无</span>';
    }
    
    projectItemContent += `
                </div>
            </div>
            <div>
                <strong>问题状态：</strong>
                <div style="margin-top: 3px;">
                    ${Object.keys(issuesByStatus).length > 0 ? Object.entries(issuesByStatus).map(([status, issues]) => `
                        <div style="margin-bottom: 3px;">
                            <span style="font-weight: 500;">${escapeHtml(status)}：</span>
                            <div style="display: inline-block;">
                                ${issues.map(issue => `<span style="display: inline-block; background: #e0f2fe; color: #0284c7; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-right: 5px; margin-bottom: 5px;">${escapeHtml(issue.bug_key || issue.key)}</span>`).join('')}
                            </div>
                        </div>
                    `).join('') : '<span style="color: #94a3b8;">无</span>'}
                </div>
            </div>
        </div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; font-size: 14px; margin-top: 10px;">
            <div>
                <strong>交付测试bug数量：</strong>
                <div style="margin-top: 3px;">
                    ${deliveryTestBugCount > 0 ? `
                        <div style="margin-bottom: 3px;">
                            <span style="font-weight: 500;">数量：${deliveryTestBugCount}</span>
                        </div>
                        <div>
                            ${deliveryTestBugs.map(issue => `<span style="display: inline-block; background: #dbeafe; color: #1d4ed8; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-right: 5px; margin-bottom: 5px;">${escapeHtml(issue.bug_key || issue.key)}</span>`).join('')}
                        </div>
                    ` : '<span style="color: #94a3b8;">无</span>'}
                </div>
            </div>
            <div>
                <strong>阻塞问题 ID：</strong>
                <div style="margin-top: 3px;">
                    ${blockingIssueCount > 0 ? `
                        <div style="margin-bottom: 3px;">
                            <span style="font-weight: 500;">数量：${blockingIssueCount}</span>
                        </div>
                        <div>
                            ${blockingIssues.map(issue => `<span style="display: inline-block; background: #fee2e2; color: #b91c1c; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-right: 5px; margin-bottom: 5px;">${escapeHtml(issue.bug_key || issue.key)}</span>`).join('')}
                        </div>
                    ` : '<span style="color: #94a3b8;">无</span>'}
                </div>
            </div>
        </div>
    `
    
    // 判断是否已存在相同项目
    let projectItem;
    if (existingProjectItem) {
        // 更新现有项目
        projectItem = existingProjectItem;
        projectItem.innerHTML = projectItemContent;
        // 移除旧的事件监听器（通过克隆替换）
        const newProjectItem = projectItem.cloneNode(true);
        projectItem.parentNode.replaceChild(newProjectItem, projectItem);
        projectItem = newProjectItem;
    } else {
        // 创建新项目列表项
        projectItem = document.createElement('div');
        projectItem.className = 'project-list-item';
        projectItem.setAttribute('data-project-key', projectKey);
        projectItem.style.cssText = `
            padding: 15px;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            margin-bottom: 10px;
            cursor: pointer;
            transition: all 0.2s ease;
        `;
        projectItem.style.backgroundColor = '#f8fafc';
        
        projectItem.onmouseover = function() {
            this.style.backgroundColor = '#e2e8f0';
        };
        projectItem.onmouseout = function() {
            this.style.backgroundColor = '#f8fafc';
        };
        
        projectItem.innerHTML = projectItemContent;
        projectListContainer.appendChild(projectItem);
    }
    
    // 点击项目列表项时更新分析摘要
    projectItem.addEventListener('click', function() {
        updateAnalysisSummary(data, projectIssues);
    });
    
    // 初始显示当前项目的分析摘要
    updateAnalysisSummary(data, projectIssues);
    
    // 渲染共性问题
    if (commonIssues.length > 0) {
        let commonIssuesHtml = `
            <p><strong>共性问题数量：</strong>${commonIssues.length}</p>
            <div style="margin-top: 10px;">
        `;
        
        commonIssues.forEach(issue => {
            const bugKey = issue.bug_key || issue.key || '未知问题';
            const summary = issue.summary || '无描述';
            const riskLevel = issue.risk_level || '未知';
            const status = issue.status || '未知';
            
            commonIssuesHtml += `
                <div style="padding: 10px; border: 1px solid #e2e8f0; border-radius: 6px; margin-bottom: 10px; background-color: #ffffff;">
                    <div style="font-weight: bold; margin-bottom: 5px;">${escapeHtml(bugKey)}</div>
                    <div style="font-size: 14px; margin-bottom: 5px;">${escapeHtml(summary)}</div>
                    <div style="font-size: 12px; color: #64748b;">
                        风险等级：${riskLevel} | 状态：${status}
                    </div>
                </div>
            `;
        });
        
        commonIssuesHtml += `</div>`;
        commonIssuesContainer.innerHTML = commonIssuesHtml;
    } else {
        commonIssuesContainer.innerHTML = '<p>暂无共性问题数据</p>';
    }
    
    // 计算饼图数据并绘制饼图
    updateCharts(projectIssues);
}

// 更新饼图
function updateCharts(projectIssues) {
    // 1. 等级分布饼图数据
    const priorityData = {
        labels: ['Block', 'Critical', 'Major', 'Minor'],
        datasets: [{
            data: [0, 0, 0, 0],
            backgroundColor: [
                '#ef4444', // 红色
                '#f97316', // 橙色
                '#f59e0b', // 黄色
                '#10b981'  // 绿色
            ]
        }]
    };
    
    // 2. 严重问题等级分布饼图数据
    const criticalData = {
        labels: ['阻塞问题', 'MP BLOCK问题'],
        datasets: [{
            data: [0, 0],
            backgroundColor: [
                '#ef4444', // 红色
                '#f97316'  // 橙色
            ]
        }]
    };
    
    // 3. 问题状态分布饼图数据
    const statusData = {
        labels: ['OPEN', 'SUBMITTED', 'IN PROGRESS', 'FIXED', 'MODIFYING', 'REOPENED', 'ABANDONED'],
        datasets: [{
            data: [0, 0, 0, 0, 0, 0, 0],
            backgroundColor: [
                '#ef4444', // 红色
                '#f97316', // 橙色
                '#f59e0b', // 黄色
                '#10b981', // 绿色
                '#3b82f6', // 蓝色
                '#8b5cf6', // 紫色
                '#6b7280'  // 灰色
            ]
        }]
    };
    
    // 4. 提交分布饼图数据
    const submissionData = {
        labels: ['含交付的bug', '不含交付的bug'],
        datasets: [{
            data: [0, 0],
            backgroundColor: [
                '#3b82f6', // 蓝色
                '#10b981'  // 绿色
            ]
        }]
    };
    
    // 统计数据
    projectIssues.forEach(issue => {
        // 等级分布
        const priority = issue.priority || issue.fields?.priority?.name || '';
        if (priority.includes('Block') || priority.includes('block')) {
            priorityData.datasets[0].data[0]++;
        } else if (priority.includes('Critical') || priority.includes('critical')) {
            priorityData.datasets[0].data[1]++;
        } else if (priority.includes('Major') || priority.includes('major')) {
            priorityData.datasets[0].data[2]++;
        } else if (priority.includes('Minor') || priority.includes('minor')) {
            priorityData.datasets[0].data[3]++;
        }
        
        // 严重问题等级分布
        const summary = issue.summary || '';
        const labels = issue.labels || [];
        if (summary.includes('阻塞') || summary.includes('Block') || labels.some(label => label.includes('阻塞') || label.includes('Block'))) {
            criticalData.datasets[0].data[0]++;
        }
        if (summary.includes('MP block') || summary.includes('MP Block') || labels.some(label => label.includes('MP block') || label.includes('MP Block'))) {
            criticalData.datasets[0].data[1]++;
        }
        
        // 问题状态分布
        const status = issue.status || issue.fields?.status?.name || '';
        const statusIndex = statusData.labels.indexOf(status.toUpperCase());
        if (statusIndex !== -1) {
            statusData.datasets[0].data[statusIndex]++;
        }
        
        // 提交分布
        if (summary.includes('交付')) {
            submissionData.datasets[0].data[0]++;
        } else {
            submissionData.datasets[0].data[1]++;
        }
    });
    
    // 绘制饼图
    drawChart('priorityChart', priorityData, '等级分布');
    drawChart('criticalChart', criticalData, '严重问题等级分布');
    drawChart('statusChart', statusData, '问题状态分布');
    drawChart('submissionChart', submissionData, '提交分布');
}

// 绘制饼图
function drawChart(canvasId, chartData, title) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    
    // 销毁之前的图表
    if (window[canvasId + 'Chart']) {
        window[canvasId + 'Chart'].destroy();
    }
    
    // 检查数据是否为0
    const totalData = chartData.datasets[0].data.reduce((sum, value) => sum + value, 0);
    
    // 清除之前的覆盖层
    const existingOverlay = document.getElementById(canvasId + '-overlay');
    if (existingOverlay) {
        existingOverlay.remove();
    }
    
    if (totalData === 0) {
        // 创建覆盖层显示暂无数据
        const overlay = document.createElement('div');
        overlay.id = canvasId + '-overlay';
        overlay.style.position = 'absolute';
        overlay.style.top = '0';
        overlay.style.left = '0';
        overlay.style.width = '100%';
        overlay.style.height = '100%';
        overlay.style.display = 'flex';
        overlay.style.alignItems = 'center';
        overlay.style.justifyContent = 'center';
        overlay.style.backgroundColor = 'rgba(255, 255, 255, 0.9)';
        overlay.style.fontSize = '14px';
        overlay.style.color = '#94a3b8';
        overlay.style.borderRadius = '8px';
        overlay.textContent = '暂无数据';
        
        // 将覆盖层添加到canvas的父容器
        canvas.parentElement.style.position = 'relative';
        canvas.parentElement.appendChild(overlay);
        return;
    }
    
    // 创建图表
    const ctx = canvas.getContext('2d');
    window[canvasId + 'Chart'] = new Chart(ctx, {
        type: 'pie',
        data: chartData,
        options: {
            responsive: false,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    enabled: true,
                    callbacks: {
                        label: function(context) {
                            const label = context.label || '';
                            const value = context.raw || 0;
                            const total = context.dataset.data.reduce((sum, val) => sum + val, 0);
                            const percentage = Math.round((value / total) * 100);
                            return `${label}: ${value} (${percentage}%)`;
                        }
                    }
                }
            }
        }
    });
}

// 更新分析摘要
function updateAnalysisSummary(data, projectIssues) {
    const analysisSummaryContainer = document.getElementById('riskAnalysisSummary');
    if (!analysisSummaryContainer) return;
    
    // 计算风险相关统计数据
    const mpBlockCount = countMPBlockIssues(projectIssues || []);
    
    // 计算阻塞问题数量
    const blockingIssues = projectIssues.filter(issue => {
        const tags = issue.tags || issue.labels || [];
        return tags.some(tag => tag.includes('阻塞') || tag.includes('MP block') || tag.includes('must resolve'));
    });
    const blockingCount = blockingIssues.length;
    
    // 计算交付测试部bug数量
    const deliveryTestBugs = projectIssues.filter(issue => {
        const summary = issue.summary || '';
        return summary.includes('交付');
    });
    const deliveryTestBugCount = deliveryTestBugs.length;
    
    // 提取所有tag及其对应的key ID
    const tagIssueMap = new Map();
    projectIssues.forEach(issue => {
        const tags = issue.tags || issue.labels || [];
        const bugKey = issue.bug_key || issue.key || '';
        if (bugKey) {
            tags.forEach(tag => {
                if (!tagIssueMap.has(tag)) {
                    tagIssueMap.set(tag, []);
                }
                if (!tagIssueMap.get(tag).includes(bugKey)) {
                    tagIssueMap.get(tag).push(bugKey);
                }
            });
        }
    });
    
    // 提取风险等级
    let riskLevel = data.structured_data?.risk_level || '评估中';
    // 从AI分析结果中提取风险等级
    if (!riskLevel || riskLevel === '评估中') {
        const analysisText = data.detailed_analysis || data.analysis || '';
        // 匹配多种格式：风险等级：中、风险等级: 【中】、风险等级：【中】等
        const riskLevelMatch = analysisText.match(/风险等级[：:]\s*【?([高|中|低])】?/);
        if (riskLevelMatch && riskLevelMatch[1]) {
            riskLevel = riskLevelMatch[1];
        }
    }
    
    let summaryContent = `
        <h4 style="margin-top: 0; margin-bottom: 15px;">${escapeHtml(data.project_key || '未知项目')} - 风险分析摘要</h4>
        <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 15px; margin-bottom: 20px;">
            <div style="background: #ffffff; padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);">
                <h5 style="margin-top: 0; margin-bottom: 10px; font-size: 14px;">风险统计</h5>
                <div style="font-size: 14px;">
                    <div style="margin-bottom: 5px;">
                        <strong>BUG风险数量：</strong>${mpBlockCount}
                    </div>
                    <div style="margin-bottom: 5px;">
                        <strong>阻塞类问题：</strong>${blockingCount}
                    </div>
                    <div style="margin-bottom: 5px;">
                        <strong>交付风险数量：</strong>${deliveryTestBugCount}
                    </div>
                </div>
            </div>
            <div style="background: #ffffff; padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);">
                <h5 style="margin-top: 0; margin-bottom: 10px; font-size: 14px;">风险等级</h5>
                <div style="font-size: 14px;">
                    <p><strong>风险等级：</strong>${escapeHtml(riskLevel)}</p>
                </div>
            </div>
            <div style="background: #ffffff; padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);">
                <h5 style="margin-top: 0; margin-bottom: 10px; font-size: 14px;">Tags</h5>
                <div style="font-size: 14px; max-height: 300px; overflow-y: auto; padding-right: 5px;">
                    ${tagIssueMap.size > 0 ? Array.from(tagIssueMap.entries()).map(([tag, issues]) => `
                        <div style="margin-bottom: 5px;">
                            <span style="display: inline-block; background: #fce7f3; color: #be185d; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-right: 5px;">${escapeHtml(tag)}</span>
                            <span style="font-size: 12px; color: #64748b;">- ${issues.map(issue => escapeHtml(issue)).join(', ')}</span>
                        </div>
                    `).join('') : '<span style="color: #94a3b8;">暂无</span>'}
                </div>
            </div>
        </div>
        <div style="background: #ffffff; padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);">
            <h5 style="margin-top: 0; margin-bottom: 10px; font-size: 14px;">风险要点</h5>
            <div style="font-size: 14px;">
                <div style="margin-bottom: 15px; padding: 10px; border: 1px solid #e2e8f0; border-radius: 6px;">
                    <div style="display: flex; align-items: center; margin-bottom: 5px;">
                        <span style="font-size: 16px; margin-right: 8px;">⚠️</span>
                        <strong>阻塞问题：</strong>
                    </div>
                    ${blockingCount > 0 ? `
                        <div style="margin-top: 5px; margin-left: 24px;">
                            <span>${blockingCount} 个</span>
                            <div style="margin-top: 5px;">
                                ${blockingIssues.map(issue => `<span style="display: inline-block; background: #fee2e2; color: #b91c1c; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-right: 5px; margin-bottom: 5px;">${escapeHtml(issue.bug_key || issue.key)}</span>`).join('')}
                            </div>
                        </div>
                    ` : '<div style="margin-top: 5px; margin-left: 24px; color: #94a3b8;">暂无</div>'}
                </div>
                <div style="margin-bottom: 15px; padding: 10px; border: 1px solid #e2e8f0; border-radius: 6px;">
                    <div style="display: flex; align-items: center; margin-bottom: 5px;">
                        <span style="font-size: 16px; margin-right: 8px;">📊</span>
                        <strong>严重问题分布：</strong>
                    </div>
                    ${(() => {
                        const criticalIssues = projectIssues.filter(issue => {
                            const priority = issue.priority || issue.fields?.priority?.name || '';
                            return priority.includes('Block') || priority.includes('block');
                        });
                        return criticalIssues.length > 0 ? `
                            <div style="margin-top: 5px; margin-left: 24px;">
                                <span>${criticalIssues.length} 个</span>
                                <div style="margin-top: 5px;">
                                    ${criticalIssues.map(issue => `<span style="display: inline-block; background: #fee2e2; color: #b91c1c; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-right: 5px; margin-bottom: 5px;">${escapeHtml(issue.bug_key || issue.key)}</span>`).join('')}
                                </div>
                            </div>
                        ` : '<div style="margin-top: 5px; margin-left: 24px; color: #94a3b8;">暂无</div>';
                    })()}
                </div>
                <div style="margin-bottom: 15px; padding: 10px; border: 1px solid #e2e8f0; border-radius: 6px;">
                    <div style="display: flex; align-items: center; margin-bottom: 5px;">
                        <span style="font-size: 16px; margin-right: 8px;">🔍</span>
                        <strong>重点风险：</strong>
                    </div>
                    ${(() => {
                        // 显示所有项目问题的KEY ID和描述
                        if (projectIssues && projectIssues.length > 0) {
                            return `
                                <div style="margin-top: 5px; margin-left: 24px; max-height: 200px; overflow-y: auto; line-height: 1.6;">
                                    ${projectIssues.map(issue => {
                                        const bugKey = issue.bug_key || issue.key || '';
                                        const summary = issue.summary || '无描述';
                                        return `<div style="margin-bottom: 8px; padding: 6px; background: #f8fafc; border-radius: 4px;">
                                            <strong>${escapeHtml(bugKey)}</strong> ${escapeHtml(summary)}
                                        </div>`;
                                    }).join('')}
                                </div>
                            `;
                        } else {
                            return '<div style="margin-top: 5px; margin-left: 24px; color: #94a3b8;">暂无</div>';
                        }
                    })()}
                </div>
                <div style="padding: 10px; border: 1px solid #e2e8f0; border-radius: 6px;">
                    <div style="display: flex; align-items: center; margin-bottom: 5px;">
                        <span style="font-size: 16px; margin-right: 8px;">📝</span>
                        <strong>结论：</strong>
                    </div>
                    ${(() => {
                        let analysisText = data.detailed_analysis || data.analysis || '';
                        if (analysisText) {
                            // 提取核心信息
                            const bugCount = projectIssues.length;
                            const openCount = projectIssues.filter(issue => issue.status === 'Open' || issue.status === 'OPEN').length;
                            const highPriorityCount = projectIssues.filter(issue => {
                                const priority = issue.priority || issue.fields?.priority?.name || '';
                                return priority.includes('Block') || priority.includes('Critical') || priority.includes('block') || priority.includes('critical');
                            }).length;
                            
                            // 生成结论
                            let summary = '';
                            let findings = [];
                            let suggestions = [];
                            
                            // 一句话总结
                            if (highPriorityCount > 0) {
                                summary = `本周风险集中在高优先级问题，共${highPriorityCount}个严重问题需要优先处理`;
                            } else if (openCount > bugCount * 0.8) {
                                summary = `问题积压严重，${openCount}个问题待处理，占比超过80%`;
                            } else {
                                summary = `项目风险相对稳定，共${bugCount}个问题，其中${openCount}个待处理`;
                            }
                            
                            // 从AI分析结果中提取风险领域和模块
                            let riskDomains = [];
                            if (analysisText) {
                                // 从AI分析中提取风险领域信息
                                const domainPatterns = [
                                    /系统稳定性/g,
                                    /4G内存/g,
                                    /ANR/g,
                                    /Native crash/g,
                                    /内存管理/g,
                                    /支付模块/g,
                                    /网络模块/g,
                                    /相机模块/g,
                                    /电池管理/g,
                                    /存储模块/g,
                                    /SystemUI/g,
                                    /IOTService/g,
                                    /zygote64/g,
                                    /healthmemory/g,
                                    /systemui/g,
                                    /iotservice/g
                                ];
                                
                                domainPatterns.forEach(pattern => {
                                    let match;
                                    while ((match = pattern.exec(analysisText)) !== null) {
                                        const domain = match[0];
                                        if (!riskDomains.includes(domain)) {
                                            riskDomains.push(domain);
                                        }
                                    }
                                });
                                
                                // 如果没有匹配到，尝试从摘要中提取
                                if (riskDomains.length === 0) {
                                    const summaryPatterns = [
                                        /ANR/g,
                                        /Native/g,
                                        /memory/g,
                                        /SystemUI/g,
                                        /IOTService/g
                                    ];
                                    projectIssues.forEach(issue => {
                                        const summary = issue.summary || '';
                                        summaryPatterns.forEach(pattern => {
                                            let match;
                                            while ((match = pattern.exec(summary)) !== null) {
                                                const domain = match[0];
                                                if (!riskDomains.includes(domain)) {
                                                    riskDomains.push(domain);
                                                }
                                            }
                                        });
                                    });
                                }
                            }
                            
                            // 核心发现
                            if (highPriorityCount > 0) {
                                findings.push('高优先级问题数量较多，可能影响项目进度和质量');
                            }
                            if (openCount > bugCount * 0.8) {
                                findings.push('问题处理效率较低，可能导致后续测试时间不足');
                            }
                            if (projectIssues.some(issue => {
                                const summary = issue.summary || '';
                                return summary.includes('交付');
                            })) {
                                findings.push('存在交付测试问题，需要关注交付质量');
                            }
                            if (riskDomains.length > 0) {
                                const domainList = riskDomains.slice(0, 3).join('、');
                                findings.push(`高风险场景：${domainList}`);
                            }
                            
                            // 行动建议
                            if (highPriorityCount > 0) {
                                suggestions.push('优先处理高优先级问题，安排专人负责');
                                suggestions.push('召开问题分析会议，找出根本原因');
                            }
                            if (openCount > bugCount * 0.8) {
                                suggestions.push('增加测试资源，提高问题处理效率');
                                suggestions.push('建立问题处理优先级机制');
                            }
                            
                            return `
                                <div style="margin-top: 5px; margin-left: 24px; max-height: 200px; overflow-y: auto; line-height: 1.6;">
                                    <p><strong>一句话总结：</strong>${summary}</p>
                                    <p><strong>核心发现：</strong></p>
                                    <ul style="margin-top: 5px; margin-bottom: 10px;">
                                        ${findings.length > 0 ? findings.map(finding => `<li>${finding}</li>`).join('') : '<li>暂无明显风险</li>'}
                                    </ul>
                                    <p><strong>行动建议：</strong></p>
                                    <ol style="margin-top: 5px;">
                                        ${suggestions.length > 0 ? suggestions.map((suggestion, index) => `<li>${suggestion}</li>`).join('') : '<li>暂无具体建议</li>'}
                                    </ol>
                                </div>
                            `;
                        } else {
                            return '<div style="margin-top: 5px; margin-left: 24px; color: #94a3b8;">暂无</div>';
                        }
                    })()}
                </div>
            </div>
        </div>
    `
    
    analysisSummaryContainer.innerHTML = summaryContent;
}

// 统计MP block问题数量
function countMPBlockIssues(issues) {
    let count = 0;
    issues.forEach(issue => {
        const summary = issue.summary || '';
        const labels = issue.labels || [];
        if (summary.includes('MP block') || summary.includes('MP Block') || labels.some(label => label.includes('MP block') || label.includes('MP Block'))) {
            count++;
        }
    });
    return count;
}

// 统计阻塞类问题数量
function countBlockingIssues(issues) {
    let count = 0;
    issues.forEach(issue => {
        const priority = issue.priority || '';
        const summary = issue.summary || '';
        if (priority.includes('Blocker') || priority.includes('blocker') || summary.includes('阻塞') || summary.includes('Block')) {
            count++;
        }
    });
    return count;
}

// 统计交付测试Bug风险数量
function countDeliveryTestIssues(issues) {
    let count = 0;
    issues.forEach(issue => {
        const summary = issue.summary || '';
        if (summary.includes('交付')) {
            count++;
        }
    });
    return count;
}

// 提取风险要点
function extractRiskPoints(text) {
    if (!text) return [];
    
    // 简单的风险要点提取逻辑
    const points = [];
    const lines = text.split('\n');
    
    for (const line of lines) {
        const trimmedLine = line.trim();
        if (trimmedLine && (trimmedLine.includes('风险') || trimmedLine.includes('问题') || trimmedLine.includes('注意') || trimmedLine.includes('建议'))) {
            points.push(trimmedLine);
        }
    }
    
    // 如果没有提取到要点，返回默认信息
    if (points.length === 0) {
        points.push('正在分析风险情况...');
    }
    
    return points.slice(0, 5); // 最多返回5个要点
}

// 显示项目详情
function showProjectDetail(data, projectIssues) {
    const modal = document.createElement('div');
    modal.className = 'modal';
    
    let modalContent = `
        <div class="modal-content">
            <span class="close">&times;</span>
            <h2>项目详情 - ${escapeHtml(data.project_key || '未知项目')}</h2>
            <div class="detail-tabs">
                <button class="tab-btn active" data-tab="bugs">Bug详情</button>
                <button class="tab-btn" data-tab="analysis">分析详情</button>
            </div>
            <div class="tab-content">
                <div id="bugs-tab" class="tab-pane active">
                    <h3>Bug详情</h3>
    `;
    
    // 添加Bug详情
    if (projectIssues && projectIssues.length > 0) {
        projectIssues.forEach((issue, index) => {
            const bugKey = issue.bug_key || issue.key || '';
            const summary = issue.summary || '';
            const status = issue.status || '';
            const priority = issue.priority || '';
            const riskLevel = issue.risk_level || '';
            const assignee = issue.assignee || '';
            const created = issue.created || '';
            
            // 从AI分析中提取bug描述和风险分析
            const bugAnalysis = extractBugAnalysis(data.detailed_analysis || data.analysis || '', bugKey);
            
            modalContent += `
                <div class="bug-detail-block">
                    <h4>${escapeHtml(bugKey)}</h4>
                    <div class="bug-detail-content">
                        <div class="detail-row">
                            <span class="detail-label">Bug描述：</span>
                            <span class="detail-value">${escapeHtml(summary || '/')}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">风险分析：</span>
                            <span class="detail-value">${escapeHtml(bugAnalysis || '/')}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">是否卡版本：</span>
                            <span class="detail-value">${escapeHtml(issue.blocking || '/')}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">状态：</span>
                            <span class="detail-value">${escapeHtml(status || '/')}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">优先级：</span>
                            <span class="detail-value">${escapeHtml(priority || '/')}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">风险等级：</span>
                            <span class="detail-value">${escapeHtml(riskLevel || '/')}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">负责人：</span>
                            <span class="detail-value">${escapeHtml(assignee || '/')}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">创建日期：</span>
                            <span class="detail-value">${escapeHtml(created || '/')}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">是否交付测试部Bug：</span>
                            <span class="detail-value">${(issue.summary || '').includes('交付') ? '是' : '否'}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">Tags：</span>
                            <span class="detail-value">${issue.tags ? issue.tags.map(tag => `<span style="display: inline-block; background: #fce7f3; color: #be185d; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-right: 5px; margin-bottom: 5px;">${escapeHtml(tag)}</span>`).join('') : '无'}</span>
                        </div>
                    </div>
                </div>
            `;
        });
    } else {
        modalContent += `<p class="empty-message">暂无Bug数据</p>`;
    }
    
    modalContent += `
                </div>
                <div id="analysis-tab" class="tab-pane">
                    <h3>分析详情</h3>
                    <div class="analysis-detail">
                        ${data.detailed_analysis || data.analysis || '<p class="empty-message">暂无分析数据</p>'}
                    </div>
                </div>
            </div>
        </div>
    `;
    
    modal.innerHTML = modalContent;
    document.body.appendChild(modal);
    
    // 关闭模态框
    const closeBtn = modal.querySelector('.close');
    closeBtn.onclick = function() {
        modal.remove();
    };
    
    // 点击模态框外部关闭
    window.onclick = function(event) {
        if (event.target == modal) {
            modal.remove();
        }
    };
    
    // 标签页切换
    const tabBtns = modal.querySelectorAll('.tab-btn');
    const tabPanes = modal.querySelectorAll('.tab-pane');
    
    tabBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            const tab = this.getAttribute('data-tab');
            
            // 移除所有活动状态
            tabBtns.forEach(b => b.classList.remove('active'));
            tabPanes.forEach(p => p.classList.remove('active'));
            
            // 添加当前活动状态
            this.classList.add('active');
            document.getElementById(tab + '-tab').classList.add('active');
        });
    });
}

// 提取Bug分析
function extractBugAnalysis(text, bugKey) {
    if (!text || !bugKey) return '';
    
    // 简单的Bug分析提取逻辑
    const lines = text.split('\n');
    let analysis = '';
    let capture = false;
    
    for (const line of lines) {
        if (line.includes(bugKey)) {
            capture = true;
            analysis += line + ' ';
        } else if (capture && line.trim() && !line.includes('Bug ID') && !line.includes('bug_id')) {
            analysis += line + ' ';
        } else if (capture && (line.trim() === '' || line.includes('Bug ID') || line.includes('bug_id'))) {
            break;
        }
    }
    
    return analysis.trim() || '';
}

// 显示Bug详情
function showBugDetail(bugId, summary, status, priority, assignee, created, tcid, labels) {
    // 创建详情模态框
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content">
            <span class="close">&times;</span>
            <h3>Bug 详情 - ${bugId}</h3>
            <div class="bug-detail">
                <p><strong>BugID：</strong>${bugId}</p>
                <p><strong>TCID：</strong>${tcid || '无'}</p>
                <p><strong>标题：</strong>${summary}</p>
                <p><strong>状态：</strong>${status}</p>
                <p><strong>优先级：</strong>${priority}</p>
                <p><strong>负责人：</strong>${assignee}</p>
                <p><strong>创建日期：</strong>${created}</p>
                <p><strong>标签：</strong>${labels || '无'}</p>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    
    // 关闭模态框
    const closeBtn = modal.querySelector('.close');
    closeBtn.onclick = function() {
        modal.remove();
    };
    
    // 点击模态框外部关闭
    window.onclick = function(event) {
        if (event.target == modal) {
            modal.remove();
        }
    };
}

// 更新漏测分析面板
function updateLeakPanel(data) {
    const leakGroups = document.getElementById('leakGroups');
    if (!leakGroups) return;
    
    // 清空现有内容
    leakGroups.innerHTML = '';
    
    // 创建AI分析卡片
    const analysisCard = document.createElement('div');
    analysisCard.className = 'project-group';
    
    let cardContent = `
        <h3>🤖 AI 漏测分析 - ${escapeHtml(data.project_key || '未知项目')}</h3>
        <div class="analysis-content">
            <p><strong>查询：</strong>${escapeHtml(data.user_query || '')}</p>
            <p><strong>未关闭问题数：</strong>${data.issue_count || 0}</p>
    `;
    
    // 添加状态分布
    if (data.status_counts && Object.keys(data.status_counts).length > 0) {
        cardContent += `
            <p><strong>状态分布：</strong></p>
            <ul>
        `;
        for (const [status, count] of Object.entries(data.status_counts)) {
            cardContent += `<li>${escapeHtml(status)}：${count}个</li>`;
        }
        cardContent += `</ul>`;
    }
    
    // 添加图表（如果有）
    if (data.chart_url) {
        cardContent += `
            <p><strong>数据可视化：</strong></p>
            <div class="chart-container">
                <img src="${data.chart_url}" alt="项目数据图表">
            </div>
        `;
    }
    
    cardContent += `</div>`;
    analysisCard.innerHTML = cardContent;
    leakGroups.appendChild(analysisCard);
    
    // 添加详细分析卡片
    if (data.analysis) {
        const detailCard = document.createElement('div');
        detailCard.className = 'project-group';
        detailCard.innerHTML = `
            <h3>📋 漏测分析报告</h3>
            <div class="analysis-content">
                <pre style="white-space: pre-wrap; font-family: inherit;">${escapeHtml(data.analysis)}</pre>
            </div>
        `;
        leakGroups.appendChild(detailCard);
    }
}

// 更新分析结果到左侧面板（兼容旧代码）
function updateAnalysisResult(data) {
    // 使用新的updatePanelByType函数
    updatePanelByType(data);
}

// 登录初始化
function initLogin() {
    const loginBtn = document.getElementById('loginBtn');
    const loginContainer = document.getElementById('loginContainer');
    const appContainer = document.getElementById('appContainer');
    const errorDiv = document.getElementById('loginError');

    loginBtn.addEventListener('click', async () => {
        const username = document.getElementById('username').value.trim();
        const password = document.getElementById('password').value;
        if (!username || !password) {
            errorDiv.textContent = '请输入用户名和密码';
            return;
        }
        try {
            await login(username, password);
            loginContainer.style.display = 'none';
            appContainer.style.display = 'flex';
            await loadAllData();
            setupNavigation();
            initSearch();
            initAIChat();
            console.log('初始化完成');
        } catch (err) {
            errorDiv.textContent = err.message;
            console.error('登录失败:', err);
        }
    });
    
    // 自动登录（方便测试）
    setTimeout(() => {
        document.getElementById('username').value = 'admin';
        document.getElementById('password').value = '123456';
        loginBtn.click();
    }, 1000);
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/[&<>]/g, function(m) {
        if (m === '&') return '&amp;';
        if (m === '<') return '&lt;';
        if (m === '>') return '&gt;';
        return m;
    });
}

document.addEventListener('DOMContentLoaded', initLogin);